"""Tool for converting documents (Word, Excel, PDF) to Markdown.

Conversions run on the **host** (not inside the sandbox) because they depend
on external programs (LibreOffice, pandoc, poppler) that may not be available
in sandbox containers.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated, Optional, Callable

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)

OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"

# Mapping from file extension → converter category.
_WORD_EXTENSIONS = frozenset({".doc", ".docx", ".rtf"})
_EXCEL_EXTENSIONS = frozenset({".xls", ".xlsx", ".csv"})
_PDF_EXTENSIONS = frozenset({".pdf"})

_SUPPORTED_EXTENSIONS = _WORD_EXTENSIONS | _EXCEL_EXTENSIONS | _PDF_EXTENSIONS
_SUPPORTED_EXTENSIONS_TEXT = ", ".join(sorted(_SUPPORTED_EXTENSIONS))

# required packages
try:
    import mammoth
except Exception as e:
    mammoth = None

try:
    import pypandoc
except Exception as e:
    pypandoc = None

try:
    import pandas
except Exception as e:
    pandas = None

try:
    import xlrd
except Exception as e:
    xlrd = None

try:
    import openpyxl
except Exception as e:
    openpyxl = None

try:
    from pdf2image import convert_from_path
except Exception as e:
    convert_from_path = None

try:
    from glmocr import GlmOcr
except Exception as e:
    GlmOcr = None


def _ensure_dependencies_for_ext(ext: str) -> None:
    deps: dict[str, dict[str, bool]] = {
        ".doc":  {"mammoth": mammoth is not None, "pypandoc": pypandoc is not None},
        ".docx": {"mammoth": mammoth is not None, "pypandoc": pypandoc is not None},
        ".rtf":  {"mammoth": mammoth is not None, "pypandoc": pypandoc is not None},
        ".xls":  {"pandas": pandas is not None, "xlrd": xlrd is not None},
        ".xlsx": {"pandas": pandas is not None, "openpyxl": openpyxl is not None},
        ".csv":  {"pandas": pandas is not None, "openpyxl": openpyxl is not None},
        ".pdf":  {"pdf2image": convert_from_path is not None, "glmocr": GlmOcr is not None},
    }
    for name, present in deps.get(ext, {}).items():
        if not present:
            raise RuntimeError(f"Missing package '{name}' for {ext} conversion. Install with: pip install {name}")


def _convert_word(actual_path: Path, output_dir: Path, markdown_filename: str) -> tuple[str, str]:
    try:
        from deerflow.uploads.third_party_upload import upload_file_without_sign

        md = _process_word(actual_path, upload_func=upload_file_without_sign)
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / markdown_filename, "w", encoding="utf-8") as f:
            f.write(md)
        return md, str(output_dir / markdown_filename)
    except Exception as e:
        raise RuntimeError(f"Error converting word to markdown: {str(e)}")


def _convert_excel(actual_path: Path, output_dir: Path, markdown_filename: str) -> tuple[str, str]:
    try:
        md = _process_excel(actual_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / markdown_filename, "w", encoding="utf-8") as f:
            f.write(md)
        return md, str(output_dir / markdown_filename)
    except Exception as e:
        raise RuntimeError(f"Error converting excel to markdown: {str(e)}")


def _convert_pdf(actual_path: Path, output_dir: Path, markdown_filename: str) -> tuple[str, str]:
    try:
        from deerflow.uploads.third_party_upload import upload_file_without_sign

        md = _process_pdf(actual_path, upload_func=upload_file_without_sign)
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / markdown_filename, "w", encoding="utf-8") as f:
            f.write(md)
        return md, str(output_dir / markdown_filename)
    except Exception as e:
        raise RuntimeError(f"Error converting pdf to markdown: {str(e)}")


def _process_word(input_path: Path, upload_func: Optional[Callable[[Path, Optional[str]], str]] = None) -> str:
    # prepare docx path
    ext = input_path.suffix.lower()
    cleanup_tmp = False
    if ext == '.doc' or ext == '.rtf':
        docx_path = _convert_to_docx(input_path)
        cleanup_tmp = True
    elif ext == '.docx':
        docx_path = input_path
    else:
        raise ValueError('Unsupported input type: ' + ext)

    try:
        html = _docx_to_html(docx_path, upload_func=upload_func)
        md = _html_to_markdown(html)
        return md
    finally:
        if cleanup_tmp:
            # delete the temp dir containing the docx
            try:
                os.remove(docx_path)
            except Exception:
                pass


def _convert_to_docx(input_path: Path) -> Path:
    ext = input_path.suffix.lower()
    if ext == '.docx':
        return input_path
    if ext != '.doc' and ext != '.rtf':
        raise ValueError('Unsupported file extension: ' + ext)

    # create a temporary output path
    tmp_dir = tempfile.mkdtemp(prefix='docx_conv_')

    # Try soffice (LibreOffice) conversion
    try:
        soffice = shutil.which("soffice") or shutil.which("soffice.exe")
        if not soffice:
            raise RuntimeError("soffice not found. Add LibreOffice to path or install LibreOffice.")
        # soffice may output into the cwd; run with --outdir
        cmd = [soffice, '--headless', '--convert-to', 'docx', '--outdir', str(tmp_dir), input_path.resolve()]
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False
        )
        # generated filename
        generated = os.path.join(tmp_dir, os.path.splitext(os.path.basename(input_path))[0] + '.docx')
        if os.path.exists(generated):
            return Path(generated)
        else:
            raise RuntimeError('soffice conversion did not produce expected file')
    except Exception as e:
        # cleanup
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError('Failed to convert .doc to .docx: ' + str(e))


def _docx_to_html(docx_path: Path, upload_func: Optional[Callable[[Path, Optional[str]], str]] = None) -> str:
    try:
        # Prefer mammoth's image inline converter wrapper when present
        images_api = getattr(mammoth, 'images', None)
        if images_api is not None and hasattr(images_api, 'inline'):
            convert_image = images_api.inline(_make_mammoth_image_converter(upload_func))
            with docx_path.open("rb") as docx_file:
                result = mammoth.convert_to_html(docx_file, convert_image=convert_image)
        else:
            with docx_path.open("rb") as docx_file:
                result = mammoth.convert_to_html(docx_file)
        html = result.value
        return html
    except Exception as e:
        raise RuntimeError('Failed to convert docx to HTML: ' + str(e))


def _html_to_markdown(html: str) -> str:
    md_text = pypandoc.convert_text(
        html,
        to="gfm",
        format="html",
    )
    return md_text


def _make_mammoth_image_converter(upload_func: Optional[Callable[[Path, Optional[str]], str]]):
    """
    Returns a function suitable for mammoth's convert_image option. The converter will
    call `upload_func(local_path, filename)` and return { 'src': url }.
    """

    def convert_image(image):
        tmp_file_path = None
        try:
            with image.open() as f:
                image_bytes = f.read()

            if not image_bytes:
                raise ValueError(
                    f"Empty image stream (content_type={getattr(image, 'content_type', None)})"
                )

            # determine filename (mammoth may provide content_type)
            extension = None
            try:
                content_type = getattr(image, 'content_type', None)
                if content_type:
                    extension = mimetypes.guess_extension(content_type) or ''
            except Exception:
                extension = ''

            with tempfile.NamedTemporaryFile(delete=False, prefix='docx_images_', suffix=extension) as tmp_file:
                tmp_file.write(image_bytes)
                tmp_file_path = tmp_file.name

            url = upload_func(Path(tmp_file_path), None)
            return {"src": url}
        except Exception:
            logger.warning(
                "mammoth image handler failed — upload_func error or OSS unavailable, ignore images",
                exc_info=True,
            )
            return {"src": ""}
        finally:
            if tmp_file_path and os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)

    return convert_image


def _process_excel(input_path: Path) -> str:
    ext = os.path.splitext(input_path)[1].lower()
    cleanup_tmp = False
    if ext == '.csv':
        xlsx_path = _convert_csv_to_xlsx(input_path)
        cleanup_tmp = True
    elif ext == '.xls':
        xlsx_path = _convert_xls_to_xlsx(input_path)
        cleanup_tmp = True
    elif ext == '.xlsx':
        xlsx_path = input_path
    else:
        raise ValueError('Unsupported input type: ' + ext)

    try:
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        markdown_content = ""

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]

            merged_cells_ranges = list(ws.merged_cells.ranges)

            if len(merged_cells_ranges) == 0:
                # --- 原始逻辑（无合并单元格） ---
                data = []
                for row in ws.iter_rows(values_only=True):
                    data.append(list(row))

                if not data:
                    continue

                df = pandas.DataFrame(data)

                if df.shape[0] >= 1:
                    raw_header = df.iloc[0].tolist()
                    header = [("" if pandas.isna(h) else str(h)) for h in raw_header]
                    df = df.iloc[1:].reset_index(drop=True)
                    df.columns = header
                else:
                    continue

                df = df.where(pandas.notna(df), "")

                rows_as_str = [[
                    ("" if (cell is None or (isinstance(cell, float) and pandas.isna(cell)) or cell == "") else
                     (cell.decode('utf-8', errors='ignore') if isinstance(cell, (bytes, bytearray)) else str(cell))
                     ) for cell in row]
                    for row in df.values.tolist()
                ]
                df = pandas.DataFrame(rows_as_str, columns=df.columns)

                sheet_md = f"## Sheet: {sheet_name}\n\n"
                sheet_md += df.to_markdown(index=False)
                markdown_content += sheet_md + "\n\n"
            else:
                # --- HTML 表格逻辑（有合并单元格） ---
                skip_cells = set()
                merge_attrs = {}

                for merged_range in merged_cells_ranges:
                    min_col, min_row, max_col, max_row = merged_range.bounds
                    colspan = max_col - min_col + 1
                    rowspan = max_row - min_row + 1
                    merge_attrs[(min_row, min_col)] = {'rowspan': rowspan, 'colspan': colspan}
                    for r in range(min_row, max_row + 1):
                        for c in range(min_col, max_col + 1):
                            if r == min_row and c == min_col:
                                continue
                            skip_cells.add((r, c))

                data = []
                for row in ws.iter_rows(values_only=True):
                    data.append(list(row))

                if not data:
                    continue

                import html

                html_lines = ["<table>"]
                for r_idx, row in enumerate(data, start=1):
                    html_lines.append("  <tr>")
                    for c_idx, cell_value in enumerate(row, start=1):
                        if (r_idx, c_idx) in skip_cells:
                            continue

                        attrs = ""
                        if (r_idx, c_idx) in merge_attrs:
                            m = merge_attrs[(r_idx, c_idx)]
                            if m['rowspan'] > 1:
                                attrs += f' rowspan="{m["rowspan"]}"'
                            if m['colspan'] > 1:
                                attrs += f' colspan="{m["colspan"]}"'

                        # 安全地转换为字符串
                        if cell_value is None or (isinstance(cell_value, float) and pandas.isna(cell_value)):
                            val_str = ""
                        elif isinstance(cell_value, (bytes, bytearray)):
                            val_str = cell_value.decode('utf-8', errors='ignore')
                        else:
                            val_str = str(cell_value)

                        # 转义 HTML 实体，并替换换行符
                        val_str = html.escape(val_str).replace("\n", "<br>")

                        tag = "th" if r_idx == 1 else "td"
                        html_lines.append(f"    <{tag}{attrs}>{val_str}</{tag}>")
                    html_lines.append("  </tr>")
                html_lines.append("</table>")

                sheet_md = f"## Sheet: {sheet_name}\n\n"
                sheet_md += "\n".join(html_lines)
                markdown_content += sheet_md + "\n\n"

        return markdown_content
    finally:
        if cleanup_tmp:
            try:
                os.remove(xlsx_path)
            except Exception:
                pass


def _convert_csv_to_xlsx(csv_path: Path) -> str:
    df = pandas.read_csv(csv_path.resolve())
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_file:
        df.to_excel(tmp_file.name, index=False)
        return tmp_file.name


def _convert_xls_to_xlsx(xls_path: Path) -> str:
    df = pandas.read_excel(xls_path.resolve(), engine='xlrd')
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_file:
        df.to_excel(tmp_file.name, index=False)
        return tmp_file.name


def _process_pdf(input_path: Path, upload_func: Optional[Callable[[Path, Optional[str]], str]] = None) -> str:
    tmpdir = tempfile.mkdtemp(prefix=f"pdf_out_{uuid.uuid4().hex}_")
    try:
        poppler = shutil.which("pdftoppm") or shutil.which("pdftoppm.exe")
        if not poppler:
            raise RuntimeError("poppler not found. Add poppler to PATH or install poppler.")

        converted_image_paths = _pdf_to_png_pages(
            pdf_path=input_path,
            out_dir=tmpdir,
            dpi=300,
            poppler_path=os.path.dirname(poppler),
            thread_count=2
        )
        markdown_content = ""

        zai_api_key = os.getenv("ZAI_API_KEY")
        if not zai_api_key:
            raise EnvironmentError("ZAI_API_KEY environment variable is required to call GlmOcr API.")

        for img_path in converted_image_paths:
            with GlmOcr(mode="maas", api_key=zai_api_key) as parser:
                parse_result = parser.parse(img_path)
                parsed_markdown_result = parse_result.markdown_result
                parsed_image_files = parse_result.image_files
                if parsed_image_files is not None and len(parsed_image_files) > 0 and upload_func is not None:
                    for img_name, img in parsed_image_files.items():
                        with tempfile.NamedTemporaryFile(delete=False,
                                                         prefix='pdf_images_',
                                                         suffix=".png",
                                                         dir=tmpdir) as tmp_file:
                            tmp_file.write(_image_to_binary(img, "PNG"))
                            tmp_file_path = tmp_file.name
                        uploaded_image_url = upload_func(Path(tmp_file_path), None)
                        parsed_markdown_result = parsed_markdown_result.replace(f"imgs/{img_name}", uploaded_image_url)
                markdown_content += parsed_markdown_result + "\n\n"
        return markdown_content
    finally:
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)


def _pdf_to_png_pages(
        pdf_path: Path,
        out_dir: str,
        dpi: int = 300,
        first_page: int | None = None,
        last_page: int | None = None,
        poppler_path: str | None = None,
        thread_count: int = 4,
) -> list[str]:
    """
    将 PDF 按页渲染为 PNG（无损），以保证质量。
    dpi: 300 通常足够清晰；扫描件或小字可 400-600。
    thread_count: 并行渲染页数（过大可能更慢或占用内存）。
    """
    pdf_name = pdf_path.stem

    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        fmt="png",
        first_page=first_page,
        last_page=last_page,
        poppler_path=poppler_path,
        thread_count=thread_count,
        use_pdftocairo=True,  # 更稳的渲染路径，常用于高质量输出
        transparent=False,  # 避免透明背景导致体积变大/边缘异常
        grayscale=False  # 保持彩色（若只要黑白可改 True）
    )

    # 注意：convert_from_path 会按页顺序返回 PIL Images
    converted_image_paths = []
    start = first_page or 1
    for i, img in enumerate(images, start=start):
        out_path = os.path.join(out_dir, f"{pdf_name}_page_{i:04d}.png")
        # PNG 无损，compress_level 越高越小但更慢（0-9）
        img.save(out_path, "PNG", compress_level=6)
        converted_image_paths.append(out_path)

    return converted_image_paths


def _image_to_binary(image, image_format) -> bytes:
    # 创建一个内存缓冲区
    buffer = io.BytesIO()
    # 将图片保存到缓冲区，指定格式
    image.save(buffer, image_format)
    # 获取二进制数据
    binary_data = buffer.getvalue()
    # 关闭缓冲区
    buffer.close()
    return binary_data


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _converter_for(extension: str):
    """Return the converter function for a given file extension."""
    ext = extension.lower()
    if ext in _WORD_EXTENSIONS:
        return _convert_word
    if ext in _EXCEL_EXTENSIONS:
        return _convert_excel
    if ext in _PDF_EXTENSIONS:
        return _convert_pdf
    return None


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


@tool("document_to_markdown", parse_docstring=True)
def document_to_markdown_tool(
        runtime: Runtime,
        file_paths: list[str],
        tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Convert document files (Word, Excel, PDF) to Markdown format.

    Conversions run on the **host machine** using external programs
    (LibreOffice, pandoc, poppler) rather than inside the sandbox.

    When to use this tool:

    - The user explicitly asks to convert a document to Markdown.
    - The user asks to upload a document to a knowledge base, learn from a
      document, or reference a document for future use — all of these imply
      the document must first be converted to readable Markdown.
    - The user uploads a Word / Excel / PDF file with any instruction
      (analyse, convert, summarise, explain) — you need the Markdown first.

    When NOT to use this tool:

    - The document is already in Markdown or plain-text format.
    - You only need to read a small portion of the file (use ``read_file``).
    - The file is an image (use ``view_image_tool`` for images).

    Args:
        file_paths: List of ``/mnt/user-data/...`` virtual paths pointing to
            Word, Excel, or PDF files (extensions .doc .docx .rtf .xls .xlsx .csv .pdf).
    """
    from deerflow.sandbox.tools import (
        get_thread_data,
        resolve_and_validate_user_data_path,
        validate_local_tool_path,
    )

    thread_data = get_thread_data(runtime)
    outputs_path = thread_data.get("outputs_path") if thread_data else None
    if not outputs_path:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        "Error: Thread outputs path is not available.",
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )
    output_dir = Path(outputs_path).resolve()
    converted: list[dict[str, str]] = []
    messages: list[str] = []

    for file_path in file_paths:
        # ── validate & resolve ──────────────────────────────────────
        ext = Path(file_path).suffix.lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            messages.append(
                f"Skipped {file_path}: unsupported format. "
                f"Supported: {_SUPPORTED_EXTENSIONS_TEXT}"
            )
            continue

        _ensure_dependencies_for_ext(ext)

        try:
            validate_local_tool_path(file_path, thread_data, read_only=True)
            actual_path = Path(
                resolve_and_validate_user_data_path(file_path, thread_data)
            )
        except Exception as exc:
            messages.append(f"Error resolving {file_path}: {exc}")
            continue

        if not actual_path.exists():
            messages.append(f"Skipped {file_path}: file not found.")
            continue

        # ── convert ──────────────────────────────────────────────────
        converter = _converter_for(ext)
        if converter is None:
            messages.append(f"Skipped {file_path}: no converter for {ext}.")
            continue

        stem = actual_path.stem
        markdown_filename = f"{stem}_converted_{int(time.time())}.md"
        virtual_md_path = f"{OUTPUTS_VIRTUAL_PREFIX}/{markdown_filename}"

        try:
            markdown_content, written_path = converter(actual_path, output_dir, markdown_filename)
        except NotImplementedError:
            messages.append(
                f"Skipped {file_path}: {ext.lstrip('.')} → Markdown "
                f"converter is not yet implemented."
            )
            continue
        except subprocess.CalledProcessError as exc:
            logger.exception("External converter failed for %s", actual_path)
            messages.append(f"Error converting {file_path}: external tool failed ({exc}).")
            continue
        except Exception:
            logger.exception("Conversion failed for %s", actual_path)
            messages.append(f"Error converting {file_path}: unexpected error.")
            continue

        converted.append(
            {
                "file_path": file_path,
                "markdown_content": markdown_content,
                "markdown_file": virtual_md_path,
            }
        )
        messages.append(
            f"Converted {file_path} → {virtual_md_path}"
        )

    if not converted and not messages:
        messages.append("No documents were converted.")

    summary = "\n".join(messages)
    return Command(
        update={
            "converted_docs": converted,
            "messages": [ToolMessage(summary, tool_call_id=tool_call_id)],
        },
    )
