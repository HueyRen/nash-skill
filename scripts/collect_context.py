#!/usr/bin/env python3
"""Build a bounded, provenance-rich local context bundle for /nash.

The collector is deliberately local-only.  It searches the current working
directory and ``~/cortex`` by default, follows a small number of local links,
and emits excerpts rather than copying entire files into the prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote, urlparse


SCHEMA_VERSION = "1.0"
TEXT_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}
PRIORITY_FILENAMES = {"_hot.md": 0, "agents.md": 1, "resolver.md": 2}
SKIP_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "cookies",
    "credentials",
    "secrets",
}
SENSITIVE_BASENAME_RE = re.compile(
    r"(?:^\.env(?:\.|$)|credential|secret|cookie|(?:^|[_\-.])tokens?(?:[_\-.]|$)|"
    r"api[_\-.]?key|private[_\-.]?key|access[_\-.]?key|^id_(?:rsa|dsa|ecdsa|ed25519))",
    re.IGNORECASE,
)
LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.-]{1,}")
CHINESE_SEQUENCE_RE = re.compile(r"[\u3400-\u9fff]{2,}")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
BARE_TEXT_PATH_RE = re.compile(
    r"(?<![\w])((?:~|\.{1,2}|/)[^\s<>'\"`]+?\.(?:md|txt|json|csv|ya?ml))",
    re.IGNORECASE,
)
STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "but",
    "can",
    "context",
    "could",
    "from",
    "have",
    "into",
    "just",
    "nash",
    "need",
    "that",
    "the",
    "their",
    "then",
    "this",
    "with",
    "what",
    "when",
    "where",
    "which",
    "will",
    "would",
    "一下",
    "一个",
    "分析",
    "帮我",
    "什么",
    "可以",
    "怎么",
    "我们",
    "我应",
    "我应该",
    "应该",
    "现在",
    "在把",
    "等待",
    "还是",
    "已有",
    "有的",
    "全部",
    "之后",
    "后再",
    "再发",
    "发给",
    "我的",
    "所有",
    "材料",
    "清单",
    "文件",
    "本地",
    "上下文",
    "相关",
    "事情",
    "问题",
    "然后",
    "这个",
    "那个",
    "需要",
}


@dataclass
class Limits:
    max_documents: int = 24
    max_total_bytes: int = 1_500_000
    max_file_bytes: int = 350_000
    max_scan_files: int = 500
    max_scan_bytes: int = 8_000_000
    max_candidate_files: int = 5_000
    max_excerpt_chars: int = 7_000
    max_excerpts_per_file: int = 5
    excerpt_context_lines: int = 4
    max_link_depth: int = 1


@dataclass
class Candidate:
    path: Path
    root: Optional[Path]
    source_kinds: Set[str] = field(default_factory=set)
    linked_from: Set[str] = field(default_factory=set)
    discovery_order: int = 0


@dataclass
class ReadResult:
    text: str
    raw: bytes
    truncated: bool
    redactions: int


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_hidden_component(path: Path, root: Optional[Path]) -> bool:
    try:
        relative = path.relative_to(root) if root else path
    except ValueError:
        relative = path
    return any(part.startswith(".") and part not in {".", ".."} for part in relative.parts)


def is_safe_text_path(path: Path, root: Optional[Path] = None) -> Tuple[bool, str]:
    """Return whether a path is eligible for local text collection."""
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return False, "unsupported_extension"
    if _is_hidden_component(path, root) and path.name.lower() not in PRIORITY_FILENAMES:
        return False, "hidden_path"
    lower_parts = {part.lower() for part in path.parts}
    if lower_parts & SKIP_DIRECTORY_NAMES:
        return False, "skipped_directory"
    if SENSITIVE_BASENAME_RE.search(path.name):
        return False, "sensitive_filename"
    return True, "eligible"


def query_terms(query: str) -> List[str]:
    """Extract useful Latin tokens and overlapping Chinese n-grams."""
    terms: List[str] = []
    for token in LATIN_TOKEN_RE.findall(query.lower()):
        token = token.strip("._-+")
        if len(token) >= 2 and token not in STOP_WORDS and "/" not in token:
            terms.append(token)
    for sequence in CHINESE_SEQUENCE_RE.findall(query):
        if sequence in STOP_WORDS:
            continue
        if len(sequence) <= 6:
            terms.append(sequence)
        for width in (2, 3):
            if len(sequence) >= width:
                terms.extend(sequence[i : i + width] for i in range(len(sequence) - width + 1))
    # Stable de-duplication avoids overweighting repeated words in the request.
    return list(dict.fromkeys(term for term in terms if term not in STOP_WORDS))


def _strip_path_punctuation(value: str) -> str:
    return value.strip().strip("'\"`()[]{}<>,;:，。；：")


def extract_explicit_paths(query: str, cwd: Path) -> List[Path]:
    """Extract exact local paths mentioned in the query without broad discovery."""
    values: List[str] = []
    values.extend(MARKDOWN_LINK_RE.findall(query))
    values.extend(match.group(1) for match in BARE_TEXT_PATH_RE.finditer(query))
    values.extend(re.findall(r"`([^`]+)`", query))
    try:
        values.extend(shlex.split(query))
    except ValueError:
        pass

    paths: List[Path] = []
    seen: Set[Path] = set()
    for raw_value in values:
        value = _strip_path_punctuation(raw_value)
        if value.startswith("file://"):
            value = unquote(urlparse(value).path)
        if not value or "://" in value or not any(mark in value for mark in ("/", "~", ".")):
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        candidate = _canonical(candidate)
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        paths.append(candidate)
    return paths


def _root_for(path: Path, roots: Sequence[Path]) -> Optional[Path]:
    for root in roots:
        try:
            path.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def _walk_root(root: Path, max_candidates: int, skip_reasons: Optional[Counter] = None) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    yielded = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for directory in sorted(dirnames):
            if directory.lower() in SKIP_DIRECTORY_NAMES or directory.startswith("."):
                if skip_reasons is not None:
                    skip_reasons["skipped_directory"] += 1
                continue
            kept_directories.append(directory)
        dirnames[:] = kept_directories
        for filename in sorted(filenames):
            path = current_path / filename
            safe, reason = is_safe_text_path(path, root)
            if not safe:
                if skip_reasons is not None:
                    skip_reasons[reason] += 1
                continue
            if path.is_symlink():
                if skip_reasons is not None:
                    skip_reasons["symlink"] += 1
                continue
            yield path
            yielded += 1
            if yielded >= max_candidates:
                return


def _filename_score(path: Path, terms: Sequence[str]) -> float:
    normalized = str(path).lower()
    return sum(18.0 for term in terms if term.lower() in normalized)


def _recency_score(mtime: float, now: float) -> float:
    days = max(0.0, (now - mtime) / 86_400.0)
    return 24.0 / (1.0 + days / 30.0)


def _archive_penalty(path: Path) -> float:
    parts = {part.lower() for part in path.parts}
    return -120.0 if parts.intersection({"archive", "archives", "backup", "backups"}) else 0.0


def _scan_order(candidate: Candidate, terms: Sequence[str], now: float) -> Tuple[float, str]:
    try:
        mtime = candidate.path.stat().st_mtime
    except OSError:
        mtime = 0.0
    priority = 0.0
    if "explicit_path" in candidate.source_kinds:
        priority += 10_000.0
    if "priority_seed" in candidate.source_kinds:
        priority += 5_000.0 - PRIORITY_FILENAMES.get(candidate.path.name.lower(), 9) * 100
    priority += _filename_score(candidate.path, terms)
    priority += _recency_score(mtime, now)
    priority += _archive_penalty(candidate.path)
    return (-priority, str(candidate.path))


HIGH_CONFIDENCE_SECRET_PATTERNS = [
    re.compile(r"\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
]


def redact_secrets(text: str) -> Tuple[str, int]:
    redactions = 0
    for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
        text, count = pattern.subn("[REDACTED_SECRET]", text)
        redactions += count
    return text, redactions


def read_text_bounded(path: Path, max_bytes: int) -> Optional[ReadResult]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except (OSError, PermissionError):
        return None
    if b"\x00" in raw[:4096]:
        return None
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            return None
    # Too many replacement characters is a strong binary/non-UTF8 signal.
    if text and text.count("\ufffd") / len(text) > 0.02:
        return None
    text, redactions = redact_secrets(text)
    return ReadResult(text=text, raw=raw, truncated=truncated, redactions=redactions)


def _term_hits(text: str, terms: Sequence[str]) -> Counter:
    lower = text.lower()
    return Counter({term: lower.count(term.lower()) for term in terms if term and term.lower() in lower})


def _merge_line_windows(windows: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    merged: List[List[int]] = []
    for start, end in sorted(windows):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def make_excerpts(
    text: str,
    terms: Sequence[str],
    source_kinds: Set[str],
    limits: Limits,
) -> List[Dict[str, object]]:
    lines = text.splitlines()
    if not lines:
        return []
    windows: List[Tuple[int, int]] = []
    reasons: Dict[Tuple[int, int], str] = {}
    lowered_terms = [term.lower() for term in terms]
    for index, line in enumerate(lines):
        lower_line = line.lower()
        matched = [term for term in lowered_terms if term in lower_line]
        if matched:
            start = max(0, index - limits.excerpt_context_lines)
            end = min(len(lines) - 1, index + limits.excerpt_context_lines)
            windows.append((start, end))
            reasons[(start, end)] = "query_match:" + ",".join(matched[:6])
    if not windows:
        if "priority_seed" in source_kinds:
            end = min(len(lines) - 1, 29)
            windows.append((0, end))
            reasons[(0, end)] = "priority_seed_overview"
        elif "explicit_path" in source_kinds or "linked" in source_kinds:
            end = min(len(lines) - 1, 19)
            windows.append((0, end))
            reasons[(0, end)] = "explicit_or_linked_overview"
    merged = _merge_line_windows(windows)[: limits.max_excerpts_per_file]
    excerpts: List[Dict[str, object]] = []
    used_chars = 0
    for start, end in merged:
        excerpt_text = "\n".join(lines[start : end + 1])
        remaining = limits.max_excerpt_chars - used_chars
        if remaining <= 0:
            break
        clipped = len(excerpt_text) > remaining
        excerpt_text = excerpt_text[:remaining]
        used_chars += len(excerpt_text)
        reason = next(
            (value for (w_start, w_end), value in reasons.items() if w_start >= start and w_end <= end),
            "merged_query_matches",
        )
        excerpts.append(
            {
                "start_line": start + 1,
                "end_line": end + 1,
                "reason": reason,
                "text": excerpt_text,
                "clipped": clipped,
            }
        )
    return excerpts


def _clip_excerpts_to_utf8_budget(
    excerpts: Sequence[Dict[str, object]],
    byte_budget: int,
) -> Tuple[List[Dict[str, object]], int, bool]:
    """Clip excerpt text to an exact UTF-8 budget without splitting code points."""
    clipped_excerpts: List[Dict[str, object]] = []
    used = 0
    budget_hit = False
    for excerpt in excerpts:
        remaining = byte_budget - used
        if remaining <= 0:
            budget_hit = True
            break
        text = str(excerpt.get("text", ""))
        encoded = text.encode("utf-8")
        clipped = len(encoded) > remaining
        if clipped:
            text = encoded[:remaining].decode("utf-8", errors="ignore")
            encoded = text.encode("utf-8")
            budget_hit = True
        if not text:
            budget_hit = True
            break
        item = dict(excerpt)
        item["text"] = text
        item["clipped"] = bool(item.get("clipped")) or clipped
        clipped_excerpts.append(item)
        used += len(encoded)
        if clipped:
            break
    return clipped_excerpts, used, budget_hit


def _local_link_values(text: str) -> Iterable[str]:
    yield from MARKDOWN_LINK_RE.findall(text)
    yield from WIKI_LINK_RE.findall(text)
    yield from (match.group(1) for match in BARE_TEXT_PATH_RE.finditer(text))


def resolve_local_links(source: Path, text: str, roots: Sequence[Path]) -> List[Path]:
    resolved: List[Path] = []
    seen: Set[Path] = set()
    for raw_value in _local_link_values(text):
        value = _strip_path_punctuation(raw_value.split("#", 1)[0])
        if not value or value.startswith(("http://", "https://", "mailto:")):
            continue
        if value.startswith("file://"):
            value = unquote(urlparse(value).path)
        target = Path(value).expanduser()
        possibilities: List[Path] = []
        if target.is_absolute():
            possibilities.append(target)
        else:
            possibilities.append(source.parent / target)
            if not target.suffix:
                possibilities.append(source.parent / target.with_suffix(".md"))
                possibilities.extend(root / target.with_suffix(".md") for root in roots)
        for possibility in possibilities:
            canonical = _canonical(possibility)
            if canonical in seen or not canonical.is_file():
                continue
            target_root = _root_for(canonical, roots)
            # Workspace documents are untrusted evidence and must not use a
            # Markdown link to escape the authorized roots. Cortex is the one
            # deliberate exception: its canonical notes commonly point to an
            # exact user-owned source file elsewhere on disk. The exact target
            # is still extension- and secret-name filtered and is never walked.
            cortex_root = _canonical(Path.home() / "cortex")
            source_is_cortex = _root_for(source, [cortex_root]) is not None
            if target_root is None and not source_is_cortex:
                continue
            safe, _ = is_safe_text_path(canonical, target_root)
            if not safe:
                continue
            # Links may resolve outside a root only when they name one exact file.
            seen.add(canonical)
            resolved.append(canonical)
            break
    return resolved


def _relative_label(path: Path, root: Optional[Path]) -> Optional[str]:
    if root is None:
        return None
    try:
        return str(path.relative_to(root))
    except ValueError:
        return None


def _content_relevance_score(hits: Counter, document_frequency: Counter, document_count: int) -> float:
    """Use a small TF-IDF-style score so long transcripts cannot win by repetition."""
    score = 0.0
    for term, count in hits.items():
        inverse_frequency = 1.0 + math.log((document_count + 1.0) / (document_frequency[term] + 1.0))
        term_frequency = 1.0 + math.log(max(1, count))
        score += 9.0 * inverse_frequency * term_frequency
    return score


def _rank_score(
    candidate: Candidate,
    hits: Counter,
    terms: Sequence[str],
    now: float,
    document_frequency: Counter,
    document_count: int,
) -> float:
    try:
        mtime = candidate.path.stat().st_mtime
    except OSError:
        mtime = 0.0
    score = _filename_score(candidate.path, terms) + _recency_score(mtime, now)
    score += _content_relevance_score(hits, document_frequency, document_count)
    score += _archive_penalty(candidate.path)
    if "explicit_path" in candidate.source_kinds:
        score += 1_000.0
    if "priority_seed" in candidate.source_kinds:
        score += 180.0 - PRIORITY_FILENAMES.get(candidate.path.name.lower(), 9) * 10
    if "linked" in candidate.source_kinds:
        score += 55.0
    return score


def collect_context(
    query: str,
    roots: Sequence[Path],
    limits: Optional[Limits] = None,
    cwd: Optional[Path] = None,
) -> Dict[str, object]:
    """Collect a bounded context bundle.  No network APIs are used."""
    limits = limits or Limits()
    cwd = _canonical(cwd or Path.cwd())
    roots = list(dict.fromkeys(_canonical(root) for root in roots if _canonical(root).exists()))
    terms = query_terms(query)
    now = datetime.now(tz=timezone.utc).timestamp()
    warnings: List[str] = []
    skip_reasons: Counter = Counter()
    candidates: Dict[Path, Candidate] = {}

    def add_candidate(path: Path, source_kind: str, root: Optional[Path], linked_from: Optional[Path] = None) -> None:
        canonical = _canonical(path)
        safe, reason = is_safe_text_path(canonical, root)
        if not safe or not canonical.is_file():
            skip_reasons[reason if not safe else "not_file"] += 1
            return
        candidate = candidates.setdefault(
            canonical,
            Candidate(path=canonical, root=root, discovery_order=len(candidates)),
        )
        candidate.source_kinds.add(source_kind)
        if linked_from:
            candidate.linked_from.add(str(linked_from))

    # Exact paths are allowed even outside the search roots, but are never expanded
    # into a broad filesystem walk.
    explicit_paths = extract_explicit_paths(query, cwd)
    for path in explicit_paths:
        if path.is_file():
            add_candidate(path, "explicit_path", _root_for(path, roots))
        elif path.is_dir():
            containing_root = _root_for(path, roots)
            if containing_root is None:
                warnings.append(f"Ignored explicit directory outside configured roots: {path}")
            else:
                roots = list(dict.fromkeys([path] + roots))

    # Seed resolver files explicitly before the broader walk. This guarantees
    # they are considered even if a root has a very large archive subtree.
    for root in roots:
        if not root.is_dir():
            continue
        try:
            root_files = {child.name.lower(): child for child in root.iterdir() if child.is_file()}
        except OSError:
            continue
        for filename in PRIORITY_FILENAMES:
            seed = root_files.get(filename)
            if seed:
                add_candidate(seed, "priority_seed", root)

    remaining_walk_slots = max(0, limits.max_candidate_files - len(candidates))
    per_root_cap = max(1, math.ceil(remaining_walk_slots / max(1, len(roots))))
    for root in roots:
        if root.is_file():
            add_candidate(root, "root_file", root.parent)
            continue
        for path in _walk_root(root, per_root_cap, skip_reasons):
            source_kind = (
                "priority_seed"
                if path.parent == root and path.name.lower() in PRIORITY_FILENAMES
                else "search"
            )
            add_candidate(path, source_kind, root)
            if len(candidates) >= limits.max_candidate_files:
                break
        if len(candidates) >= limits.max_candidate_files:
            warnings.append("Candidate enumeration reached max_candidate_files; remaining paths were not ranked.")
            break

    ordered = sorted(candidates.values(), key=lambda candidate: _scan_order(candidate, terms, now))
    scanned_bytes = 0
    scanned_files = 0
    evaluated: List[Tuple[float, Candidate, ReadResult, Counter]] = []
    read_cache: Dict[Path, ReadResult] = {}

    link_reserve = (
        min(limits.max_documents * 2, max(1, limits.max_scan_files // 4))
        if limits.max_link_depth > 0 and limits.max_scan_files > 1
        else 0
    )
    initial_scan_limit = max(1, limits.max_scan_files - link_reserve)
    for candidate in ordered:
        if scanned_files >= initial_scan_limit or scanned_bytes >= limits.max_scan_bytes:
            break
        remaining_scan_bytes = limits.max_scan_bytes - scanned_bytes
        read_limit = min(limits.max_file_bytes, remaining_scan_bytes)
        if read_limit <= 0:
            break
        result = read_text_bounded(candidate.path, read_limit)
        if result is None:
            skip_reasons["unreadable_or_binary"] += 1
            continue
        scanned_files += 1
        scanned_bytes += len(result.raw)
        read_cache[candidate.path] = result
        hits = _term_hits(result.text, terms)
        try:
            stat = candidate.path.stat()
        except OSError:
            skip_reasons["stat_failed"] += 1
            continue
        # A preliminary score is sufficient for choosing which files to link-
        # trace. Final ranking below uses corpus-aware term weights.
        preliminary = _filename_score(candidate.path, terms) + _recency_score(stat.st_mtime, now)
        preliminary += _archive_penalty(candidate.path)
        preliminary += sum((1.0 + math.log(max(1, count))) * 8.0 for count in hits.values())
        if "explicit_path" in candidate.source_kinds:
            preliminary += 1_000.0
        if "priority_seed" in candidate.source_kinds:
            preliminary += 180.0 - PRIORITY_FILENAMES.get(candidate.path.name.lower(), 9) * 10
        evaluated.append((preliminary, candidate, result, hits))

    # Follow local links from the strongest initial sources, once by default.
    if limits.max_link_depth > 0:
        initial_ranked = sorted(evaluated, key=lambda item: (-item[0], str(item[1].path)))
        link_source_limit = min(len(initial_ranked), limits.max_documents * 3)
        for _, source_candidate, source_result, _ in initial_ranked[:link_source_limit]:
            for linked_path in resolve_local_links(source_candidate.path, source_result.text, roots):
                if linked_path in read_cache or scanned_files >= limits.max_scan_files:
                    existing = candidates.get(linked_path)
                    if existing:
                        existing.source_kinds.add("linked")
                        existing.linked_from.add(str(source_candidate.path))
                    continue
                if scanned_bytes >= limits.max_scan_bytes:
                    break
                link_root = _root_for(linked_path, roots)
                linked_candidate = Candidate(
                    path=linked_path,
                    root=link_root,
                    source_kinds={"linked"},
                    linked_from={str(source_candidate.path)},
                    discovery_order=len(candidates),
                )
                read_limit = min(limits.max_file_bytes, limits.max_scan_bytes - scanned_bytes)
                linked_result = read_text_bounded(linked_path, read_limit)
                if linked_result is None:
                    skip_reasons["linked_unreadable_or_binary"] += 1
                    continue
                scanned_files += 1
                scanned_bytes += len(linked_result.raw)
                read_cache[linked_path] = linked_result
                hits = _term_hits(linked_result.text, terms)
                try:
                    stat = linked_path.stat()
                    score = 80.0 + _filename_score(linked_path, terms) + _recency_score(stat.st_mtime, now)
                except OSError:
                    score = 80.0
                score += sum((1.0 + math.log(max(1, count))) * 8.0 for count in hits.values())
                evaluated.append((score, linked_candidate, linked_result, hits))

    document_frequency: Counter = Counter()
    for _, _, _, hits in evaluated:
        document_frequency.update(hits.keys())
    rescored = [
        (
            _rank_score(
                candidate,
                hits,
                terms,
                now,
                document_frequency,
                max(1, len(evaluated)),
            ),
            candidate,
            result,
            hits,
        )
        for _, candidate, result, hits in evaluated
    ]
    ranked = sorted(rescored, key=lambda item: (-item[0], str(item[1].path)))
    priority_items = [item for item in ranked if "priority_seed" in item[1].source_kinds]
    ordinary_items = [item for item in ranked if "priority_seed" not in item[1].source_kinds]
    selected = (priority_items + ordinary_items)[: limits.max_documents]
    total_selected_bytes = 0
    output_budget_hit = False
    documents: List[Dict[str, object]] = []
    for score, candidate, result, hits in selected:
        if total_selected_bytes >= limits.max_total_bytes:
            output_budget_hit = True
            break
        remaining = limits.max_total_bytes - total_selected_bytes
        try:
            stat = candidate.path.stat()
        except OSError:
            continue
        excerpts = make_excerpts(result.text, terms, candidate.source_kinds, limits)
        if not excerpts and not candidate.source_kinds.intersection({"priority_seed", "explicit_path", "linked"}):
            # A pure search candidate without a textual hit adds no useful prompt context.
            continue
        excerpts, excerpt_bytes, clipped_by_total_budget = _clip_excerpts_to_utf8_budget(excerpts, remaining)
        if not excerpts:
            output_budget_hit = True
            break
        total_selected_bytes += excerpt_bytes
        output_budget_hit = output_budget_hit or clipped_by_total_budget
        documents.append(
            {
                "path": str(candidate.path),
                "root": str(candidate.root) if candidate.root else None,
                "relative_path": _relative_label(candidate.path, candidate.root),
                "source_kinds": sorted(candidate.source_kinds),
                "linked_from": sorted(candidate.linked_from),
                "score": round(score, 3),
                "matched_terms": dict(hits.most_common()),
                "provenance": {
                    "modified_at": _utc_iso(stat.st_mtime),
                    "size_bytes": stat.st_size,
                    "bytes_read": len(result.raw),
                    "content_sha256": hashlib.sha256(result.raw).hexdigest(),
                    "read_truncated": result.truncated,
                    "secret_redactions": result.redactions,
                },
                "excerpts": excerpts,
            }
        )

    if scanned_bytes >= limits.max_scan_bytes:
        warnings.append("Content scan reached max_scan_bytes; remaining candidates were not inspected.")
    if len(documents) >= limits.max_documents:
        warnings.append("Output reached max_documents; lower-ranked context was omitted.")
    if output_budget_hit or total_selected_bytes >= limits.max_total_bytes:
        warnings.append("Output excerpts reached max_total_bytes; remaining excerpt text was omitted.")
    if not terms:
        warnings.append("The query contained no searchable terms; only priority or explicit context may be useful.")

    return {
        "schema_version": SCHEMA_VERSION,
        "query": query,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "search_scope": {
            "roots": [str(root) for root in roots],
            "explicit_paths": [str(path) for path in explicit_paths],
            "network_access": False,
        },
        "query_terms": terms,
        "limits": vars(limits),
        "stats": {
            "candidate_files": len(candidates),
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
            "selected_documents": len(documents),
            "selected_bytes": total_selected_bytes,
            "skip_reasons": dict(skip_reasons),
        },
        "warnings": warnings,
        "documents": documents,
    }


def default_roots(cwd: Path) -> List[Path]:
    roots = [cwd]
    cortex = Path.home() / "cortex"
    if cortex.exists():
        roots.append(cortex)
    return roots


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="Natural-language decision or negotiation question.")
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Additional/narrowed search root. Repeatable. Defaults to cwd and ~/cortex.",
    )
    parser.add_argument("--output", type=Path, help="Write JSON here; stdout when omitted.")
    parser.add_argument("--max-documents", type=int, default=Limits.max_documents)
    parser.add_argument("--max-total-bytes", type=int, default=Limits.max_total_bytes)
    parser.add_argument("--max-file-bytes", type=int, default=Limits.max_file_bytes)
    parser.add_argument("--max-scan-files", type=int, default=Limits.max_scan_files)
    parser.add_argument("--max-scan-bytes", type=int, default=Limits.max_scan_bytes)
    parser.add_argument("--max-candidate-files", type=int, default=Limits.max_candidate_files)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cwd = _canonical(Path.cwd())
    roots = [_canonical(Path(root)) for root in args.root] if args.root else default_roots(cwd)
    positive_values = {
        "max_documents": args.max_documents,
        "max_total_bytes": args.max_total_bytes,
        "max_file_bytes": args.max_file_bytes,
        "max_scan_files": args.max_scan_files,
        "max_scan_bytes": args.max_scan_bytes,
        "max_candidate_files": args.max_candidate_files,
    }
    invalid = [name for name, value in positive_values.items() if value <= 0]
    if invalid:
        parser.error("Limits must be positive: " + ", ".join(invalid))
    limits = Limits(**positive_values)
    bundle = collect_context(args.query, roots=roots, limits=limits, cwd=cwd)
    serialized = json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = _canonical(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8")
    else:
        sys.stdout.write(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
