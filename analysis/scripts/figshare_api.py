from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import logging
import os
import re
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import requests
from dotenv import load_dotenv
from tqdm.auto import tqdm

WD = Path(__file__).parent.resolve()
ROOT = Path("/".join(WD.parts[: WD.parts.index("abstract_reasoning") + 1]))

BASE_URL = "https://api.figshare.com/v2"
ARTICLE_ID = 29573534
CHUNK_SIZE = 1024 * 1024
REQUEST_TIMEOUT = 60
RATE_LIMIT_RETRIES = 6
RATE_LIMIT_BACKOFF_SECONDS = 30

REMOTE_FILE_COMPLETE_STATUSES = {None, "available"}
REMOTE_FILE_PENDING_STATUSES = {"ic_checking"}

DEFAULT_EXCLUDE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
}
DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}

CONTENT_DISPOSITION_FILENAME_RE = re.compile(r'filename="?([^";]+)"?')


@dataclass(frozen=True)
class LocalFile:
    path: Path
    figshare_name: str
    size: int
    md5: str


@dataclass(frozen=True)
class FileComparison:
    name: str
    status: str
    local_size: int | None = None
    remote_size: int | None = None
    local_md5: str | None = None
    remote_md5: str | None = None
    remote_id: int | None = None
    detail: str = ""


class FigshareAPIError(RuntimeError):
    def __init__(
        self,
        method: str,
        url: str,
        status_code: int,
        *,
        message: str,
        code: str | None = None,
        raw_body: str | None = None,
    ):
        self.method = method
        self.url = url
        self.status_code = status_code
        self.api_message = message
        self.code = code
        self.raw_body = raw_body
        super().__init__(self.user_message())

    def user_message(self) -> str:
        code = f" [{self.code}]" if self.code else ""
        return f"{self.method} failed with {self.status_code}{code}: {self.api_message}"

    def log_message(self) -> str:
        return (
            f"{self.method} {self.url} failed with {self.status_code}; "
            f"code={self.code!r}; message={self.api_message!r}; body={self.raw_body!r}"
        )


class FigshareClient:
    def __init__(self, token: str, base_url: str = BASE_URL):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"token {token}"})

    def request(
        self,
        method: str,
        url_or_endpoint: str,
        *,
        data: bytes | str | None = None,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> Any:
        url = self._resolve_url(url_or_endpoint)
        for attempt in range(1, RATE_LIMIT_RETRIES + 1):
            response = self.session.request(
                method,
                url,
                data=data,
                json=json_data,
                params=params,
                timeout=timeout,
            )
            if response.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                break

            retry_after = response.headers.get("Retry-After")
            try:
                sleep_s = int(retry_after) if retry_after else RATE_LIMIT_BACKOFF_SECONDS
            except ValueError:
                sleep_s = RATE_LIMIT_BACKOFF_SECONDS
            print(
                f"Rate limit hit for {method} {url}; retrying in {sleep_s}s "
                f"({attempt}/{RATE_LIMIT_RETRIES - 1})"
            )
            time.sleep(sleep_s)

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip()
            code = None
            message = body or response.reason
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                code = payload.get("code")
                message = payload.get("message") or payload.get("error") or message

            raise FigshareAPIError(
                method=method,
                url=url,
                status_code=response.status_code,
                message=message,
                code=code,
                raw_body=body,
            ) from exc

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.content

    def _resolve_url(self, url_or_endpoint: str) -> str:
        if url_or_endpoint.startswith("http"):
            return url_or_endpoint
        endpoint = url_or_endpoint.lstrip("/")
        if endpoint.startswith("v2/"):
            endpoint = endpoint.removeprefix("v2/")
        return f"{self.base_url}/{endpoint}"

    def get_article(self, article_id: int) -> dict[str, Any]:
        return self.request("GET", f"account/articles/{article_id}")

    def update_article_metadata(
        self,
        article_id: int,
        metadata: dict[str, Any],
    ) -> None:
        self.request("PATCH", f"account/articles/{article_id}", json_data=metadata)

    def list_files(self, article_id: int, page_size: int = 100) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.request(
                "GET",
                f"account/articles/{article_id}/files",
                params={"page": page, "page_size": page_size},
            )
            files.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return files

    def delete_file(self, article_id: int, file_id: int) -> None:
        self.request("DELETE", f"account/articles/{article_id}/files/{file_id}")

    def initiate_upload(self, article_id: int, file: LocalFile) -> dict[str, Any]:
        result = self.request(
            "POST",
            f"account/articles/{article_id}/files",
            json_data={
                "name": file.figshare_name,
                "size": file.size,
                "md5": file.md5,
            },
        )
        location = result.get("location")
        if not location:
            raise RuntimeError(f"Figshare did not return an upload location: {result}")
        return self.request("GET", location)

    def upload_parts(
        self,
        file: LocalFile,
        file_info: dict[str, Any],
        *,
        retries: int = 3,
        logger: logging.Logger | None = None,
    ) -> None:
        upload_url = file_info["upload_url"]
        upload_info = self.request("GET", upload_url)
        parts = upload_info["parts"]
        if logger is not None:
            logger.info(
                "Uploading %s in %s part(s)", file.figshare_name, len(parts)
            )

        with file.path.open("rb") as stream:
            for index, part in enumerate(parts, start=1):
                part_no = part["partNo"]
                start = part["startOffset"]
                end = part["endOffset"]
                n_bytes = end - start + 1

                stream.seek(start)
                data = stream.read(n_bytes)
                if len(data) != n_bytes:
                    raise RuntimeError(
                        f"Read {len(data)} bytes for {file.path}, expected {n_bytes}"
                    )

                self._upload_part_with_retries(
                    upload_url=f"{upload_url}/{part_no}",
                    data=data,
                    retries=retries,
                    logger=logger,
                    context=f"{file.figshare_name} part {index}/{len(parts)}",
                )
                if logger is not None:
                    logger.info(
                        "Part complete: %s part %s/%s (%s-%s)",
                        file.figshare_name,
                        index,
                        len(parts),
                        start,
                        end,
                    )

    def _upload_part_with_retries(
        self,
        upload_url: str,
        data: bytes,
        *,
        retries: int,
        logger: logging.Logger | None = None,
        context: str = "upload part",
    ) -> None:
        for attempt in range(1, retries + 1):
            try:
                self.request(
                    "PUT",
                    upload_url,
                    data=data,
                    timeout=max(REQUEST_TIMEOUT, len(data) // CHUNK_SIZE * 30),
                )
                return
            except RuntimeError as exc:
                if attempt == retries:
                    raise
                sleep_s = 2**attempt
                if logger is not None:
                    if isinstance(exc, FigshareAPIError):
                        logger.warning(
                            "%s failed on attempt %s/%s; retrying in %ss: %s",
                            context,
                            attempt,
                            retries,
                            sleep_s,
                            exc.log_message(),
                        )
                    else:
                        logger.exception(
                            "%s failed on attempt %s/%s; retrying in %ss",
                            context,
                            attempt,
                            retries,
                            sleep_s,
                        )
                else:
                    print(
                        f"    upload part failed; retrying in {sleep_s}s: "
                        f"{format_exception_for_user(exc)}"
                    )
                time.sleep(sleep_s)

    def complete_upload(self, article_id: int, file_id: int) -> None:
        self.request("POST", f"account/articles/{article_id}/files/{file_id}")

    def publish_article(self, article_id: int) -> None:
        self.request("POST", f"account/articles/{article_id}/publish")

    def download_article(
        self,
        article_id: int,
        output_path: Path,
        *,
        folder_path: str | None = None,
        overwrite: bool = False,
        logger: logging.Logger | None = None,
    ) -> Path:
        params = {"folder_path": folder_path} if folder_path else None
        url = self._resolve_url(f"account/articles/{article_id}/download")
        if logger is not None:
            logger.info(
                "Starting article download: article_id=%s, output_path=%s, folder_path=%s",
                article_id,
                output_path,
                folder_path,
            )

        with self.session.get(
            url,
            params=params,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                body = response.text.strip()
                code = None
                message = body or response.reason
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    code = payload.get("code")
                    message = payload.get("message") or message
                raise FigshareAPIError(
                    method="GET",
                    url=url,
                    status_code=response.status_code,
                    message=message,
                    code=code,
                    raw_body=body,
                ) from exc

            output_path = resolve_download_output_path(output_path, response.headers)
            if output_path.exists() and not overwrite:
                raise FileExistsError(
                    f"Download output already exists: {output_path}. "
                    "Use --overwrite to replace it."
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            with output_path.open("wb") as stream:
                progress = tqdm(
                    total=total_size or None,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc="Downloading article",
                )
                with progress:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        stream.write(chunk)
                        downloaded += len(chunk)
                        progress.update(len(chunk))

            if logger is not None:
                logger.info(
                    "Completed article download: article_id=%s, output_path=%s, bytes=%s",
                    article_id,
                    output_path,
                    downloaded,
                )

        return output_path


def read_1password_secret(reference: str) -> str:
    """Resolve a 1Password secret reference using the `op` CLI."""
    try:
        result = subprocess.run(
            ["op", "read", reference],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("The 1Password CLI (`op`) was not found on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or "Unknown 1Password error"
        raise RuntimeError(f"Could not retrieve the Figshare token: {message}") from exc

    token = result.stdout.strip()
    if not token:
        raise RuntimeError("1Password returned an empty token")

    return token


def load_token() -> str:
    load_dotenv(ROOT / ".secrets")

    if token := os.environ.get("FIGSHARE_TOKEN"):
        return token

    token_ref = os.environ.get("FIGSHARE_TOKEN_REF")
    if token_ref:
        return read_1password_secret(token_ref)

    raise RuntimeError(
        "Set FIGSHARE_TOKEN or FIGSHARE_TOKEN_REF in the environment or .secrets."
    )


def filename_from_content_disposition(value: str | None) -> str | None:
    if not value:
        return None
    match = CONTENT_DISPOSITION_FILENAME_RE.search(value)
    if not match:
        return None
    return Path(match.group(1)).name


def resolve_download_output_path(
    output_path: Path,
    headers: requests.structures.CaseInsensitiveDict,
) -> Path:
    if output_path.exists() and output_path.is_dir():
        filename = filename_from_content_disposition(headers.get("content-disposition"))
        return output_path / (filename or "figshare_article_download.zip")
    return output_path


def format_bids_entity(prefix: str, value: str | int) -> str:
    value_str = str(value).strip()
    full_prefix = f"{prefix}-"
    if value_str.startswith(full_prefix):
        value_str = value_str.removeprefix(full_prefix)
    if value_str.isdigit():
        value_str = f"{int(value_str):02d}"
    return f"{full_prefix}{value_str}"


def download_folder_paths_from_args(args: argparse.Namespace) -> list[str]:
    if args.folder_path and args.download_subject:
        raise SystemExit("Use either --folder-path or --download-subject, not both.")
    if args.download_session and not args.download_subject:
        raise SystemExit("--download-session requires --download-subject.")

    if args.download_subject:
        subjects = [
            format_bids_entity("sub", subject)
            for subject in args.download_subject
        ]
        if args.download_session:
            sessions = [
                format_bids_entity("ses", session)
                for session in args.download_session
            ]
            return [
                f"{subject}/{session}"
                for subject in subjects
                for session in sessions
            ]
        return subjects

    return [args.folder_path] if args.folder_path else []


def download_label(folder_paths: list[str]) -> str | None:
    if not folder_paths:
        return None
    if len(folder_paths) == 1:
        return folder_paths[0]
    clean_parts = [
        re.sub(r"[^A-Za-z0-9_.-]+", "_", folder_path).strip("_")
        for folder_path in folder_paths
    ]
    return f"selection_{len(folder_paths)}_" + "_".join(clean_parts[:4])


def default_download_output_path(article_id: int, folder_paths: list[str]) -> Path:
    label = download_label(folder_paths)
    if not label:
        return Path.cwd() / f"figshare_article_{article_id}.zip"
    clean_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_")
    return Path.cwd() / f"figshare_article_{article_id}_{clean_name}.zip"


def resolve_partial_download_output_path(
    output_path: Path,
    article_id: int,
    folder_paths: list[str],
) -> Path:
    if output_path.exists() and output_path.is_dir():
        return output_path / default_download_output_path(article_id, folder_paths).name
    return output_path


def default_log_file(label: str = "operation") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "operation"
    return WD / "logs" / f"figshare_{clean_label}_{timestamp}.log"


def setup_file_logger(
    log_file: Path | None = None,
    *,
    label: str = "operation",
) -> logging.Logger:
    log_file = log_file or default_log_file(label)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("figshare_api")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.FileHandler(log_file)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.info("Logging Figshare details to %s", log_file)
    print(f"Figshare log: {log_file}")
    return logger


def format_exception_for_user(exc: BaseException) -> str:
    if isinstance(exc, FigshareAPIError):
        return exc.user_message()
    return str(exc)


def parse_keywords(values: list[str]) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()

    for value in values:
        for raw_keyword in value.split(","):
            keyword = raw_keyword.strip()
            if not keyword or keyword in seen:
                continue
            keywords.append(keyword)
            seen.add(keyword)

    return keywords


def plain_text_to_html(text: str) -> str:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text.strip())]
    escaped_paragraphs = [
        html.escape(paragraph).replace("\n", "<br>\n")
        for paragraph in paragraphs
        if paragraph
    ]
    return "\n\n".join(f"<p>{paragraph}</p>" for paragraph in escaped_paragraphs)


def markdown_to_html(text: str) -> str:
    try:
        import markdown
    except ModuleNotFoundError:
        return plain_text_to_html(text)

    return markdown.markdown(
        text,
        extensions=["extra", "sane_lists"],
        output_format="html",
    )


def resolve_description_format(
    requested_format: str,
    source_path: Path | None,
) -> str:
    if requested_format != "auto":
        return requested_format
    if source_path is None:
        return "text"

    suffix = source_path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".html", ".htm"}:
        return "html"
    return "text"


def format_description(
    text: str,
    *,
    requested_format: str,
    source_path: Path | None = None,
) -> str:
    description_format = resolve_description_format(requested_format, source_path)
    if description_format == "markdown":
        return markdown_to_html(text)
    if description_format == "text":
        return plain_text_to_html(text)
    if description_format == "html":
        return text

    raise ValueError(f"Unsupported description format: {description_format}")


def article_metadata_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.description is not None and args.description_file is not None:
        raise SystemExit("Use either --description or --description-file, not both.")

    metadata: dict[str, Any] = {}
    if args.description is not None:
        metadata["description"] = format_description(
            args.description,
            requested_format=args.description_format,
        )
    if args.description_file is not None:
        description_file = args.description_file.expanduser()
        metadata["description"] = format_description(
            description_file.read_text(encoding="utf-8"),
            requested_format=args.description_format,
            source_path=description_file,
        )

    keywords = parse_keywords(args.keywords)
    if keywords:
        metadata["keywords"] = keywords

    if not metadata:
        raise SystemExit(
            "Provide at least one metadata field: --description, "
            "--description-file, or --keywords."
        )

    return metadata


def summarize_metadata_update(metadata: dict[str, Any]) -> None:
    print("\nMetadata update:")
    if "description" in metadata:
        description = metadata["description"]
        print(f"  description: {len(description)} character(s)")
    if "keywords" in metadata:
        print(f"  keywords: {', '.join(metadata['keywords'])}")


def update_article_metadata(
    client: FigshareClient,
    article_id: int,
    metadata: dict[str, Any],
    *,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> None:
    summarize_metadata_update(metadata)
    if logger is not None:
        logger.info(
            "Updating article metadata: article_id=%s, fields=%s",
            article_id,
            sorted(metadata),
        )
        if "description" in metadata:
            logger.info(
                "Description length: %s character(s)",
                len(metadata["description"]),
            )
        if "keywords" in metadata:
            logger.info("Keywords: %s", metadata["keywords"])

    if dry_run:
        print("Dry run: skipping metadata update.")
        return

    client.update_article_metadata(article_id, metadata)
    if logger is not None:
        logger.info("Completed metadata update: article_id=%s", article_id)
    print("Updated article metadata.")


def log_exception(logger: logging.Logger | None, message: str, exc: BaseException) -> None:
    if logger is None:
        return
    if isinstance(exc, FigshareAPIError):
        logger.error("%s: %s", message, exc.log_message())
    else:
        logger.exception(message)


def summarize_failures(failures: list[tuple[str, BaseException]]) -> str:
    if not failures:
        return ""

    grouped: dict[str, int] = {}
    for _, exc in failures:
        grouped[format_exception_for_user(exc)] = (
            grouped.get(format_exception_for_user(exc), 0) + 1
        )

    parts = [
        f"{count}x {message}"
        for message, count in sorted(grouped.items(), key=lambda item: item[1], reverse=True)
    ]
    failed_names = ", ".join(name for name, _ in failures[:10])
    return (
        f"{len(failures)} failure(s). Error summary: {'; '.join(parts[:5])}. "
        f"First failed files: {failed_names}"
    )


def file_md5_and_size(path: Path) -> tuple[str, int]:
    md5 = hashlib.md5()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            size += len(chunk)
            md5.update(chunk)
    return md5.hexdigest(), size


def should_skip(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if path.name in DEFAULT_EXCLUDE_NAMES:
        return True
    if path.name.startswith("._"):
        return True
    return any(part in DEFAULT_EXCLUDE_DIRS for part in relative.parts)


def collect_local_files(dataset_root: Path) -> list[LocalFile]:
    dataset_root = dataset_root.resolve()
    if not dataset_root.exists():
        raise FileNotFoundError(f"Dataset root does not exist: {dataset_root}")
    if not dataset_root.is_dir():
        raise NotADirectoryError(f"Dataset root is not a directory: {dataset_root}")

    local_files: list[LocalFile] = []
    for path in sorted(dataset_root.rglob("*")):
        if not path.is_file() or should_skip(path, dataset_root):
            continue

        relative = path.relative_to(dataset_root)
        figshare_name = PurePosixPath(*relative.parts).as_posix()
        md5, size = file_md5_and_size(path)
        local_files.append(
            LocalFile(
                path=path,
                figshare_name=figshare_name,
                size=size,
                md5=md5,
            )
        )

    return local_files


def print_file_tree(files: list[dict[str, Any] | LocalFile]) -> None:
    tree: dict[str, dict] = {}

    for file in files:
        name = file.figshare_name if isinstance(file, LocalFile) else file["name"]
        path = PurePosixPath(name)
        current = tree
        for part in path.parts:
            current = current.setdefault(part, {})

    def print_branch(branch: dict[str, dict], prefix: str = "") -> None:
        entries = sorted(branch.items())
        for index, (name, children) in enumerate(entries):
            is_last = index == len(entries) - 1
            connector = "`-- " if is_last else "|-- "
            print(f"{prefix}{connector}{name}")
            child_prefix = prefix + ("    " if is_last else "|   ")
            print_branch(children, child_prefix)

    print_branch(tree)


def remote_files_in_folder(
    remote_files: list[dict[str, Any]],
    folder_path: str,
) -> list[dict[str, Any]]:
    prefix = folder_path.strip("/")
    return [
        file
        for file in remote_files
        if file["name"] == prefix or file["name"].startswith(f"{prefix}/")
    ]


def remote_files_in_folders(
    remote_files: list[dict[str, Any]],
    folder_paths: list[str],
) -> list[dict[str, Any]]:
    selected: dict[int | str, dict[str, Any]] = {}
    for folder_path in folder_paths:
        for file in remote_files_in_folder(remote_files, folder_path):
            key = file.get("id", file["name"])
            selected[key] = file
    return sorted(selected.values(), key=lambda file: file["name"])


def raise_for_stream_response(response: requests.Response, method: str, url: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text.strip()
        code = None
        message = body or response.reason
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            code = payload.get("code")
            message = payload.get("message") or message
        raise FigshareAPIError(
            method=method,
            url=url,
            status_code=response.status_code,
            message=message,
            code=code,
            raw_body=body,
        ) from exc


def download_remote_files_as_zip(
    client: FigshareClient,
    remote_files: list[dict[str, Any]],
    output_path: Path,
    *,
    article_id: int,
    folder_paths: list[str],
    overwrite: bool = False,
    logger: logging.Logger | None = None,
) -> Path:
    selected_files = remote_files_in_folders(remote_files, folder_paths)
    if not selected_files:
        raise FileNotFoundError(
            f"No remote files found under folder_path(s)={folder_paths!r}"
        )

    output_path = resolve_partial_download_output_path(
        output_path, article_id, folder_paths
    )
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Download output already exists: {output_path}. Use --overwrite to replace it."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    total_size = sum(int(file.get("size") or 0) for file in selected_files)
    if logger is not None:
        logger.info(
            "Starting partial download: folder_paths=%s, files=%s, total_bytes=%s, output=%s",
            folder_paths,
            len(selected_files),
            total_size,
            output_path,
        )

    print(
        f"Partial download: {len(selected_files)} file(s) from "
        f"{', '.join(folder_paths)} "
        f"({total_size / 1024**3:.2f} GiB)"
    )

    with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_STORED) as archive:
        progress = tqdm(
            total=total_size or None,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc="Downloading files",
        )
        with progress:
            for file in selected_files:
                file_name = file["name"]
                download_url = file.get("download_url")
                if not download_url:
                    raise RuntimeError(f"Remote file has no download_url: {file_name}")

                progress.set_postfix_str(file_name[-40:])
                if logger is not None:
                    logger.info("Downloading remote file: %s (%s)", file_name, file.get("id"))

                with client.session.get(
                    download_url,
                    stream=True,
                    timeout=REQUEST_TIMEOUT,
                ) as response:
                    raise_for_stream_response(response, "GET", download_url)
                    with archive.open(file_name, mode="w") as archive_file:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if not chunk:
                                continue
                            archive_file.write(chunk)
                            progress.update(len(chunk))

    if logger is not None:
        logger.info("Completed partial download: %s", output_path)
    return output_path


def summarize_article(article: dict[str, Any]) -> None:
    print(f"Title: {article['title']}")
    print(f"Article ID: {article['id']}")
    print(f"DOI: {article.get('doi')}")
    print(f"Published: {article.get('published_date')}")
    print(f"Version: {article.get('version')}")


def summarize_local_files(files: list[LocalFile], *, show_tree: bool = True) -> None:
    total_size = sum(file.size for file in files)
    print(f"Local files: {len(files)} ({total_size / 1024**3:.2f} GiB)")
    if files and show_tree:
        print_file_tree(files)


def remote_file_md5(file: dict[str, Any]) -> str | None:
    md5 = file.get("computed_md5") or file.get("supplied_md5")
    return str(md5).lower() if md5 else None


def remote_file_status(file: dict[str, Any]) -> str | None:
    status = file.get("status")
    return str(status).lower() if status else None


def remote_file_is_available(file: dict[str, Any]) -> bool:
    status = remote_file_status(file)
    return status in REMOTE_FILE_COMPLETE_STATUSES


def remote_file_is_pending(file: dict[str, Any]) -> bool:
    return remote_file_status(file) in REMOTE_FILE_PENDING_STATUSES


def remote_file_is_present(file: dict[str, Any]) -> bool:
    return remote_file_is_available(file) or remote_file_is_pending(file)


def remote_file_matches_local(remote_file: dict[str, Any], local_file: LocalFile) -> bool:
    if not remote_file_is_present(remote_file):
        return False

    if int(remote_file.get("size", -1)) != local_file.size:
        return False

    md5 = remote_file_md5(remote_file)
    if md5 is None:
        return True

    return md5 == local_file.md5.lower()


def remote_files_by_name(
    remote_files: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    by_name: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()

    for file in remote_files:
        name = file["name"]
        if name in by_name:
            duplicates.add(name)
            continue
        by_name[name] = file

    return by_name, duplicates


def compare_local_remote_files(
    local_files: list[LocalFile],
    remote_files: list[dict[str, Any]],
) -> list[FileComparison]:
    remote_by_name, duplicate_remote_names = remote_files_by_name(remote_files)
    local_by_name = {file.figshare_name: file for file in local_files}
    comparisons: list[FileComparison] = []

    for local_file in local_files:
        remote_file = remote_by_name.get(local_file.figshare_name)
        if remote_file is None:
            comparisons.append(
                FileComparison(
                    name=local_file.figshare_name,
                    status="missing_remote",
                    local_size=local_file.size,
                    local_md5=local_file.md5,
                    detail="Local file is not present on Figshare.",
                )
            )
            continue

        remote_md5 = remote_file_md5(remote_file)
        remote_size = int(remote_file.get("size", -1))
        remote_status = remote_file_status(remote_file)
        if local_file.figshare_name in duplicate_remote_names:
            status = "duplicate_remote"
            detail = "Multiple remote files have this name; comparison is ambiguous."
        elif not remote_file_is_present(remote_file):
            status = "incomplete_remote"
            detail = f"Remote file status is {remote_status!r}; it may need replacement."
        elif remote_size != local_file.size:
            status = "size_mismatch"
            detail = "Local and remote file sizes differ."
        elif remote_md5 and remote_md5 != local_file.md5.lower():
            status = "md5_mismatch"
            detail = "Local and remote MD5 checksums differ."
        elif remote_file_is_pending(remote_file):
            status = "pending_remote"
            detail = f"Remote file status is {remote_status!r}; Figshare is still checking it."
        else:
            status = "match"
            detail = ""

        comparisons.append(
            FileComparison(
                name=local_file.figshare_name,
                status=status,
                local_size=local_file.size,
                remote_size=remote_size,
                local_md5=local_file.md5,
                remote_md5=remote_md5,
                remote_id=remote_file.get("id"),
                detail=detail,
            )
        )

    for remote_file in remote_files:
        name = remote_file["name"]
        if name not in local_by_name:
            comparisons.append(
                FileComparison(
                    name=name,
                    status="extra_remote",
                    remote_size=remote_file.get("size"),
                    remote_md5=remote_file_md5(remote_file),
                    remote_id=remote_file.get("id"),
                    detail="Remote file is not present locally.",
                )
            )

    return comparisons


def comparison_counts(comparisons: list[FileComparison]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for comparison in comparisons:
        counts[comparison.status] = counts.get(comparison.status, 0) + 1
    return counts


def comparison_to_dict(comparison: FileComparison) -> dict[str, Any]:
    return {
        "name": comparison.name,
        "status": comparison.status,
        "local_size": comparison.local_size,
        "remote_size": comparison.remote_size,
        "local_md5": comparison.local_md5,
        "remote_md5": comparison.remote_md5,
        "remote_id": comparison.remote_id,
        "detail": comparison.detail,
    }


def write_comparison_report(
    comparisons: list[FileComparison],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [comparison_to_dict(comparison) for comparison in comparisons]
    suffix = output.suffix.lower()

    if suffix == ".json":
        output.write_text(json.dumps(rows, indent=2))
        return

    fieldnames = [
        "name",
        "status",
        "local_size",
        "remote_size",
        "local_md5",
        "remote_md5",
        "remote_id",
        "detail",
    ]
    delimiter = "\t" if suffix == ".tsv" else ","
    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def print_comparison_summary(
    comparisons: list[FileComparison],
    *,
    sample_size: int = 20,
) -> None:
    counts = comparison_counts(comparisons)
    total = len(comparisons)

    print("\nComparison summary:")
    for status in (
        "match",
        "missing_remote",
        "extra_remote",
        "size_mismatch",
        "md5_mismatch",
        "duplicate_remote",
    ):
        print(f"- {status}: {counts.get(status, 0)}")
    print(f"- total comparison rows: {total}")

    problems = [comparison for comparison in comparisons if comparison.status != "match"]
    if not problems:
        print("\nLocal and remote files match.")
        return

    print(f"\nFirst {min(sample_size, len(problems))} non-matching rows:")
    for comparison in problems[:sample_size]:
        print(
            f"- [{comparison.status}] {comparison.name} "
            f"(local_size={comparison.local_size}, remote_size={comparison.remote_size})"
        )


def files_to_resume_upload(
    local_files: list[LocalFile],
    remote_files: list[dict[str, Any]],
) -> tuple[list[LocalFile], list[LocalFile], list[tuple[LocalFile, dict[str, Any]]]]:
    remote_by_name, duplicate_remote_names = remote_files_by_name(remote_files)
    skipped: list[LocalFile] = []
    mismatched: list[tuple[LocalFile, dict[str, Any]]] = []
    missing: list[LocalFile] = []

    for local_file in local_files:
        remote_file = remote_by_name.get(local_file.figshare_name)
        if remote_file is None:
            missing.append(local_file)
            continue

        if local_file.figshare_name in duplicate_remote_names:
            continue

        if remote_file_matches_local(remote_file, local_file):
            skipped.append(local_file)
        else:
            mismatched.append((local_file, remote_file))

    return missing, skipped, mismatched


def confirm_or_exit(args: argparse.Namespace, remote_files: list[dict[str, Any]]) -> None:
    if args.dry_run or args.yes or not remote_files:
        return

    if args.delete_only:
        print("\nThis will delete every existing file attached to the Figshare article.")
        expected = "DELETE ONLY"
    else:
        print(
            "\nThis will delete every existing file attached to the Figshare article "
            "before uploading the local dataset."
        )
        expected = "DELETE AND UPLOAD"

    answer = input(f"Type {expected} to continue: ").strip()
    if answer != expected:
        raise SystemExit("Aborted.")


def delete_remote_files(
    client: FigshareClient,
    article_id: int,
    files: list[dict[str, Any]],
    *,
    dry_run: bool,
    logger: logging.Logger | None = None,
) -> None:
    if not files:
        print("No remote files to delete.")
        return

    print(f"\nRemote files to delete: {len(files)}")

    if dry_run:
        print_file_tree(files)
        print("Dry run: skipping remote deletion.")
        return

    if logger is not None:
        logger.info(
            "Starting remote deletion batch: article_id=%s, files=%s",
            article_id,
            len(files),
        )

    failures: list[tuple[str, BaseException]] = []
    for file in tqdm(files, total=len(files), unit="file", desc="Deleting files"):
        file_id = file["id"]
        file_name = file.get("name", str(file_id))
        try:
            client.delete_file(article_id, file_id)
            if logger is not None:
                logger.info("Deleted remote file: %s (%s)", file_name, file_id)
        except BaseException as exc:
            failures.append((file_name, exc))
            tqdm.write(
                f"Failed to delete {file_name} ({file_id}): "
                f"{format_exception_for_user(exc)}"
            )
            log_exception(logger, f"Failed to delete remote file: {file_name} ({file_id})", exc)

    if failures:
        raise RuntimeError(f"Deletion failed: {summarize_failures(failures)}")


def upload_one_file(
    client: FigshareClient,
    article_id: int,
    file: LocalFile,
    *,
    retries: int,
    logger: logging.Logger | None = None,
) -> int:
    if logger is not None:
        logger.info(
            "Starting upload: %s (%s bytes, md5=%s)",
            file.figshare_name,
            file.size,
            file.md5,
        )
    file_info = client.initiate_upload(article_id, file)
    client.upload_parts(file, file_info, retries=retries, logger=logger)
    client.complete_upload(article_id, file_info["id"])
    if logger is not None:
        logger.info("Completed upload: %s (id=%s)", file.figshare_name, file_info["id"])
    return int(file_info["id"])


def upload_one_file_with_new_client(
    token: str,
    article_id: int,
    file: LocalFile,
    *,
    retries: int,
    logger: logging.Logger | None = None,
) -> int:
    client = FigshareClient(token=token)
    return upload_one_file(client, article_id, file, retries=retries, logger=logger)


def upload_dataset(
    client: FigshareClient,
    article_id: int,
    files: list[LocalFile],
    *,
    dry_run: bool,
    retries: int,
    workers: int = 1,
    logger: logging.Logger | None = None,
) -> None:
    if not files:
        raise RuntimeError("No local files found to upload.")

    total_size = sum(file.size for file in files)
    print(f"\nLocal files to upload: {len(files)} ({total_size / 1024**3:.2f} GiB)")

    if dry_run:
        print_file_tree(files)
        print("Dry run: skipping upload.")
        return

    if logger is not None:
        logger.info(
            "Starting upload batch: article_id=%s, files=%s, total_bytes=%s, workers=%s",
            article_id,
            len(files),
            total_size,
            workers,
        )

    if workers > 1:
        print(f"Uploading with {workers} worker threads.")
        failures: list[tuple[str, BaseException]] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    upload_one_file_with_new_client,
                    client.token,
                    article_id,
                    file,
                    retries=retries,
                    logger=logger,
                ): (index, file)
                for index, file in enumerate(files, start=1)
            }

            progress = tqdm(
                as_completed(futures),
                total=len(futures),
                unit="file",
                desc="Uploading files",
            )
            for future in progress:
                index, file = futures[future]
                try:
                    file_id = future.result()
                    progress.set_postfix_str(file.figshare_name[-40:])
                    if logger is not None:
                        logger.info(
                            "Completed %s/%s: %s (id=%s)",
                            index,
                            len(files),
                            file.figshare_name,
                            file_id,
                        )
                except BaseException as exc:
                    failures.append((file.figshare_name, exc))
                    tqdm.write(
                        f"Failed {index}/{len(files)}: {file.figshare_name}: "
                        f"{format_exception_for_user(exc)}"
                    )
                    log_exception(logger, f"Failed upload: {file.figshare_name}", exc)

        if failures:
            raise RuntimeError(f"Upload failed: {summarize_failures(failures)}")
        return

    progress = tqdm(files, total=len(files), unit="file", desc="Uploading files")
    for index, file in enumerate(progress, start=1):
        progress.set_postfix_str(file.figshare_name[-40:])
        try:
            file_id = upload_one_file(client, article_id, file, retries=retries, logger=logger)
        except BaseException as exc:
            log_exception(logger, f"Failed upload: {file.figshare_name}", exc)
            raise RuntimeError(
                f"Upload failed for {file.figshare_name}: "
                f"{format_exception_for_user(exc)}"
            ) from exc
        if logger is not None:
            logger.info(
                "Completed %s/%s: %s (id=%s)",
                index,
                len(files),
                file.figshare_name,
                file_id,
            )


def resume_upload_dataset(
    client: FigshareClient,
    article_id: int,
    local_files: list[LocalFile],
    remote_files: list[dict[str, Any]],
    *,
    dry_run: bool,
    retries: int,
    replace_mismatched: bool,
    workers: int,
    logger: logging.Logger | None = None,
) -> None:
    missing, skipped, mismatched = files_to_resume_upload(local_files, remote_files)
    _, duplicate_remote_names = remote_files_by_name(remote_files)
    to_upload = list(missing)

    print("\nResume upload summary:")
    print(f"- Local files: {len(local_files)}")
    print(f"- Remote files: {len(remote_files)}")
    print(f"- Already present remotely: {len(skipped)}")
    print(f"- Missing files to upload: {len(missing)}")
    print(f"- Remote files needing replacement: {len(mismatched)}")
    print(f"- Duplicate remote names: {len(duplicate_remote_names)}")

    if mismatched:
        print("\nRemote files needing replacement:")
        for local_file, remote_file in mismatched[:20]:
            if remote_file_is_available(remote_file):
                reason = (
                    f"local size={local_file.size}, "
                    f"remote size={remote_file.get('size')}"
                )
            else:
                reason = f"status={remote_file.get('status')}"
            print(
                f"- {local_file.figshare_name} ({reason})"
            )
        if len(mismatched) > 20:
            print(f"... and {len(mismatched) - 20} more")

        if replace_mismatched:
            print(
                "\n--replace-mismatched is set; these remote files "
                "will be replaced."
            )
            to_upload.extend(local_file for local_file, _ in mismatched)
        else:
            print(
                "\nThese files are not uploaded by default to avoid creating "
                "duplicate names. Re-run with --replace-mismatched to delete and "
                "replace those remote files."
            )

    if duplicate_remote_names:
        print("\nDuplicate remote names prevent reliable matching:")
        for name in sorted(duplicate_remote_names)[:20]:
            print(f"- {name}")
        if len(duplicate_remote_names) > 20:
            print(f"... and {len(duplicate_remote_names) - 20} more")

    if not to_upload:
        print("\nNothing to upload; all local files are already present remotely.")
        return

    if dry_run:
        print("\nDry run: files that would be uploaded:")
        print_file_tree(to_upload)
        if replace_mismatched and mismatched:
            print("\nDry run: remote files that would be deleted first:")
            for _, remote_file in mismatched[:20]:
                print(f"- {remote_file['name']} ({remote_file['id']})")
        return

    if replace_mismatched:
        for _, remote_file in mismatched:
            client.delete_file(article_id, remote_file["id"])
            print(
                f"Deleted remote file for replacement: "
                f"{remote_file['name']} ({remote_file['id']})"
            )

    upload_dataset(
        client,
        article_id,
        to_upload,
        dry_run=False,
        retries=retries,
        workers=workers,
        logger=logger,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replace every file on a Figshare article with files from a local BIDS "
            "dataset directory."
        )
    )
    parser.add_argument(
        "dataset_root",
        nargs="?",
        type=Path,
        help="Local BIDS dataset root to upload.",
    )
    parser.add_argument(
        "--article-id",
        type=int,
        default=ARTICLE_ID,
        help=f"Figshare article ID. Default: {ARTICLE_ID}",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Only list the remote Figshare article files.",
    )
    parser.add_argument(
        "--delete-only",
        action="store_true",
        help="Delete all remote files but do not upload local files.",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help=(
            "Compare local files against remote Figshare files by relative name, "
            "size, and MD5, then exit."
        ),
    )
    parser.add_argument(
        "--compare-output",
        type=Path,
        default=None,
        help=(
            "Optional comparison report path. Supports .csv, .tsv, and .json. "
            "Only used with --compare."
        ),
    )
    parser.add_argument(
        "--resume-upload",
        action="store_true",
        help=(
            "Upload only local files missing from Figshare. Does not delete remote "
            "files unless --replace-mismatched is also set."
        ),
    )
    parser.add_argument(
        "--replace-mismatched",
        action="store_true",
        help=(
            "With --resume-upload, delete remote files whose names match local files "
            "but whose size/MD5 differs, then upload the local replacements."
        ),
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download the Figshare article archive and exit.",
    )
    parser.add_argument(
        "--update-metadata",
        action="store_true",
        help=(
            "Update Figshare article metadata and exit. Supports --description, "
            "--description-file, and --keywords."
        ),
    )
    parser.add_argument(
        "--description",
        type=str,
        default=None,
        help="Article description text to set. Only used with --update-metadata.",
    )
    parser.add_argument(
        "--description-file",
        type=Path,
        default=None,
        help=(
            "Path to a text, Markdown, or HTML file whose contents should become "
            "the article description. Only used with --update-metadata."
        ),
    )
    parser.add_argument(
        "--description-format",
        choices=["auto", "markdown", "html", "text"],
        default="auto",
        help=(
            "Input format for --description or --description-file. Default: auto "
            "(.md/.markdown -> markdown, .html/.htm -> html, otherwise text)."
        ),
    )
    parser.add_argument(
        "--keywords",
        type=str,
        nargs="+",
        default=[],
        help=(
            "Article keywords to set. Accepts space-separated values and/or "
            "comma-separated groups. Only used with --update-metadata."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Download output file or directory. Only used with --download. "
            "Default: ./figshare_article_<article-id>.zip"
        ),
    )
    parser.add_argument(
        "--folder-path",
        type=str,
        default=None,
        help=(
            "Remote folder path to download as a locally built zip. "
            "Only used with --download."
        ),
    )
    parser.add_argument(
        "--download-subject",
        type=str,
        nargs="+",
        default=[],
        help=(
            "Download one or more BIDS subject folders, e.g. 17 18 or sub-17 sub-18. "
            "Shortcut for --folder-path sub-XX."
        ),
    )
    parser.add_argument(
        "--download-session",
        type=str,
        nargs="+",
        default=[],
        help=(
            "Download one or more BIDS sessions within each --download-subject, e.g. 3 4 or ses-03 ses-04. "
            "Shortcut for --folder-path sub-XX/ses-YY."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow --download to replace an existing output file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without deleting or uploading.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the destructive confirmation prompt.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the article after upload completion.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Upload retries per file part. Default: 3.",
    )
    parser.add_argument(
        "--upload-workers",
        type=int,
        default=1,
        help=(
            "Number of files to upload concurrently. Default: 1. "
            "Use 2-4 for large datasets; higher values may hit rate limits."
        ),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help=(
            "Figshare operation log file. Default: analysis/scripts/logs/"
            "figshare_<operation>_<timestamp>.log"
        ),
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_arg_parser()
    if argv is not None:
        return parser.parse_args(argv)

    if "ipykernel" in Path(sys.argv[0]).name:
        args, unknown = parser.parse_known_args()
        if unknown:
            print(f"Ignoring notebook kernel arguments: {' '.join(unknown)}")
        return args

    return parser.parse_args()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.download_subject or args.download_session:
        args.download = True
    if args.description is not None or args.description_file is not None or args.keywords:
        args.update_metadata = True
    if args.upload_workers < 1:
        raise SystemExit("--upload-workers must be at least 1.")

    if (
        not args.list
        and not args.delete_only
        and not args.download
        and not args.update_metadata
        and not args.resume_upload
        and not args.compare
        and args.dataset_root is None
    ):
        raise SystemExit(
            "dataset_root is required unless --list, --delete-only, --download, "
            "or --update-metadata is used."
        )
    if args.resume_upload and args.dataset_root is None:
        raise SystemExit("dataset_root is required with --resume-upload.")
    if args.compare and args.dataset_root is None:
        raise SystemExit("dataset_root is required with --compare.")

    client = FigshareClient(token=load_token())
    article = client.get_article(args.article_id)
    summarize_article(article)

    if args.update_metadata:
        metadata = article_metadata_from_args(args)
        logger = None if args.dry_run else setup_file_logger(args.log_file, label="metadata")
        try:
            update_article_metadata(
                client,
                args.article_id,
                metadata,
                dry_run=args.dry_run,
                logger=logger,
            )
        except Exception as exc:
            log_exception(logger, "Metadata update failed", exc)
            raise RuntimeError(
                f"Metadata update failed: {format_exception_for_user(exc)}"
            ) from exc
        if args.publish:
            if args.dry_run:
                print("Dry run: skipping publish.")
            else:
                client.publish_article(args.article_id)
                print("Published article.")
        return

    remote_files = client.list_files(args.article_id)

    print(f"\nCurrent remote files: {len(remote_files)}")
    if remote_files and (args.list or args.dry_run):
        print_file_tree(remote_files)

    if args.list:
        return

    if args.download:
        folder_paths = download_folder_paths_from_args(args)
        output_path = args.output.expanduser() if args.output else default_download_output_path(
            args.article_id, folder_paths
        )
        if args.dry_run:
            print(f"Dry run: would download article to {output_path.resolve()}")
            if folder_paths:
                print(f"Dry run: folder_paths={', '.join(folder_paths)}")
            return

        logger = setup_file_logger(args.log_file, label="download")
        try:
            if folder_paths:
                downloaded_path = download_remote_files_as_zip(
                    client,
                    remote_files,
                    output_path,
                    article_id=args.article_id,
                    folder_paths=folder_paths,
                    overwrite=args.overwrite,
                    logger=logger,
                )
            else:
                downloaded_path = client.download_article(
                    args.article_id,
                    output_path,
                    overwrite=args.overwrite,
                    logger=logger,
                )
        except Exception as exc:
            log_exception(logger, "Download failed", exc)
            raise RuntimeError(f"Download failed: {format_exception_for_user(exc)}") from exc
        if folder_paths:
            print(f"Downloaded partial archive to zip file: {downloaded_path.resolve()}")
        else:
            print(f"Downloaded article archive to file: {downloaded_path.resolve()}")
        return

    if args.compare:
        local_files = collect_local_files(args.dataset_root)
        print()
        summarize_local_files(local_files, show_tree=False)
        comparisons = compare_local_remote_files(local_files, remote_files)
        print_comparison_summary(comparisons)
        if args.compare_output is not None:
            write_comparison_report(comparisons, args.compare_output)
            print(f"\nWrote comparison report to {args.compare_output}")
        return

    if args.resume_upload:
        logger = None if args.dry_run else setup_file_logger(args.log_file, label="upload")
        local_files = collect_local_files(args.dataset_root)
        print()
        summarize_local_files(local_files, show_tree=args.dry_run)
        resume_upload_dataset(
            client,
            args.article_id,
            local_files,
            remote_files,
            dry_run=args.dry_run,
            retries=args.retries,
            replace_mismatched=args.replace_mismatched,
            workers=args.upload_workers,
            logger=logger,
        )
        if args.publish:
            if args.dry_run:
                print("Dry run: skipping publish.")
            else:
                client.publish_article(args.article_id)
                print("Published article.")
        return

    local_files = [] if args.delete_only else collect_local_files(args.dataset_root)
    if local_files:
        print()
        summarize_local_files(local_files, show_tree=args.dry_run)

    confirm_or_exit(args, remote_files)

    logger = None if args.dry_run else setup_file_logger(
        args.log_file,
        label="delete" if args.delete_only else "replace_upload",
    )

    delete_remote_files(
        client,
        args.article_id,
        remote_files,
        dry_run=args.dry_run,
        logger=logger,
    )

    if not args.delete_only:
        upload_dataset(
            client,
            args.article_id,
            local_files,
            dry_run=args.dry_run,
            retries=args.retries,
            workers=args.upload_workers,
            logger=logger,
        )

    if args.publish:
        if args.dry_run:
            print("Dry run: skipping publish.")
        else:
            client.publish_article(args.article_id)
            print("Published article.")


if __name__ == "__main__":
    main()
