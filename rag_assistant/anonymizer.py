from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import Iterable, Sequence

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from lxml import etree


KEY_FORMAT = "atlas-anonymization-key"
KEY_VERSION = 1
SUPPORTED_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".eml"}
LEGACY_EXTENSIONS = {".doc", ".xls", ".ppt", ".msg"}

ENTITY_LABELS = {
    "PERSON": "ФИО",
    "EMAIL": "Электронная почта",
    "PHONE": "Телефон",
    "PASSPORT": "Паспорт",
    "SNILS": "СНИЛС",
    "TAX_ID": "ИНН / КПП / ОГРН",
    "BANK_CARD": "Банковская карта",
    "IP_ADDRESS": "IP-адрес",
    "ADDRESS": "Адрес",
    "ORGANIZATION": "Организация",
    "CUSTOM": "Пользовательское значение",
}
DEFAULT_CATEGORIES = list(ENTITY_LABELS)


@dataclass(frozen=True)
class Finding:
    category: str
    value: str
    occurrences: int
    first_position: int

    @property
    def label(self) -> str:
        return ENTITY_LABELS.get(self.category, self.category)


@dataclass(frozen=True)
class AnonymizedResult:
    filename: str
    content: bytes
    key_filename: str
    key_content: bytes
    replacements: int


@dataclass(frozen=True)
class RestoredResult:
    filename: str
    content: bytes
    replacements: int


@dataclass(frozen=True)
class _PatternSpec:
    category: str
    regex: re.Pattern[str]
    group: int = 0
    validator: object | None = None


def _luhn_valid(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        digit = int(character)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


PATTERNS = [
    _PatternSpec("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I)),
    _PatternSpec(
        "PHONE",
        re.compile(r"(?<!\d)(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)"),
    ),
    _PatternSpec("SNILS", re.compile(r"(?<!\d)\d{3}[- ]\d{3}[- ]\d{3}[ -]\d{2}(?!\d)")),
    _PatternSpec(
        "PASSPORT",
        re.compile(r"(?<!\d)(?:серия\s*)?\d{2}\s?\d{2}\s*(?:№\s*)?\d{6}(?!\d)", re.I),
    ),
    _PatternSpec(
        "TAX_ID",
        re.compile(r"(?:ИНН|КПП|ОГРН(?:ИП)?)\s*[:№-]?\s*(\d{9,15})", re.I),
        group=1,
    ),
    _PatternSpec(
        "BANK_CARD",
        re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
        validator=_luhn_valid,
    ),
    _PatternSpec(
        "IP_ADDRESS",
        re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)"),
    ),
    _PatternSpec(
        "ADDRESS",
        re.compile(
            r"(?:адрес(?: регистрации| проживания)?|место жительства)\s*[:\-]\s*([^\r\n;]{8,180})",
            re.I,
        ),
        group=1,
    ),
    _PatternSpec(
        "ORGANIZATION",
        re.compile(r"\b(?:ООО|АО|ПАО|ЗАО|НКО)\s+[«\"]?[^\r\n,;:\"»]{2,100}[»\"]?", re.I),
    ),
    _PatternSpec(
        "PERSON",
        re.compile(
            r"(?:Ф\.?И\.?О\.?|сотрудник|получатель|отправитель|контактное лицо|ответственный)"
            r"\s*[:\-]\s*([А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){1,2})",
            re.I,
        ),
        group=1,
    ),
    _PatternSpec(
        "PERSON",
        re.compile(
            r"\b([А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+)?)\s*<[^>]+@[^>]+>",
        ),
        group=1,
    ),
]


def _extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension in LEGACY_EXTENSIONS:
        raise ValueError(
            f"Формат {extension} нельзя безопасно восстановить без потери структуры. "
            "Сохраните файл как DOCX, XLSX, PPTX или EML."
        )
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Поддерживаются DOCX, XLSX, PPTX и Outlook EML.")
    return extension


def _office_member_supported(extension: str, member: str) -> bool:
    normalized = member.replace("\\", "/")
    if not normalized.endswith(".xml"):
        return False
    if extension == ".docx":
        return normalized.startswith("word/")
    if extension == ".pptx":
        return normalized.startswith("ppt/")
    return normalized == "xl/sharedStrings.xml" or normalized.startswith("xl/worksheets/") or normalized.startswith("xl/comments")


def _text_groups(root: etree._Element, extension: str) -> list[list[etree._Element]]:
    if extension == ".docx":
        containers = root.xpath("//*[local-name()='p']")
        text_name = "t"
    elif extension == ".pptx":
        containers = root.xpath("//*[local-name()='p']")
        text_name = "t"
    else:
        containers = root.xpath("//*[local-name()='si' or local-name()='is' or local-name()='comment']")
        text_name = "t"
    groups = []
    for container in containers:
        nodes = container.xpath(f".//*[local-name()='{text_name}']")
        if nodes:
            groups.append(nodes)
    return groups


def _office_texts(content: bytes, extension: str) -> list[str]:
    texts: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            for member in package.namelist():
                if not _office_member_supported(extension, member):
                    continue
                root = etree.fromstring(package.read(member))
                for nodes in _text_groups(root, extension):
                    value = "".join(node.text or "" for node in nodes)
                    if value.strip():
                        texts.append(value)
    except (zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        raise ValueError("Файл Office повреждён или защищён паролем.") from exc
    return texts


def _eml_texts(content: bytes) -> list[str]:
    message = BytesParser(policy=policy.default).parsebytes(content)
    texts: list[str] = []
    for header in ("from", "to", "cc", "bcc", "reply-to", "subject"):
        texts.extend(str(value) for value in message.get_all(header, []))
    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_maintype() == "text":
            try:
                texts.append(part.get_content())
            except (LookupError, UnicodeDecodeError):
                continue
        filename = part.get_filename()
        if filename and Path(filename).suffix.lower() in {".docx", ".xlsx", ".pptx"}:
            payload = part.get_payload(decode=True) or b""
            texts.extend(_office_texts(payload, Path(filename).suffix.lower()))
    return texts


def extract_texts(content: bytes, filename: str) -> list[str]:
    extension = _extension(filename)
    return _eml_texts(content) if extension == ".eml" else _office_texts(content, extension)


def find_sensitive_data(
    content: bytes,
    filename: str,
    categories: Iterable[str] | None = None,
    custom_terms: Iterable[str] | None = None,
) -> list[Finding]:
    enabled = set(categories or DEFAULT_CATEGORIES)
    texts = extract_texts(content, filename)
    combined = "\n".join(texts)
    found: dict[tuple[str, str], tuple[int, int]] = {}
    for spec in PATTERNS:
        if spec.category not in enabled:
            continue
        for match in spec.regex.finditer(combined):
            value = match.group(spec.group).strip()
            if not value or (spec.validator and not spec.validator(value)):
                continue
            key = (spec.category, value)
            count, first = found.get(key, (0, match.start(spec.group)))
            found[key] = (count + 1, min(first, match.start(spec.group)))
    if "CUSTOM" in enabled:
        for term in custom_terms or []:
            value = term.strip()
            if not value:
                continue
            matches = list(re.finditer(re.escape(value), combined, re.I))
            if matches:
                found[("CUSTOM", value)] = (len(matches), matches[0].start())
    return sorted(
        (Finding(category, value, count, first) for (category, value), (count, first) in found.items()),
        key=lambda item: (item.first_position, item.category, item.value.casefold()),
    )


def _matches(text: str, replacements: dict[str, str]) -> list[tuple[int, int, str]]:
    candidates: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for original in sorted(replacements, key=len, reverse=True):
        for match in re.finditer(re.escape(original), text, re.I):
            start, end = match.span()
            if any(start < other_end and end > other_start for other_start, other_end in occupied):
                continue
            occupied.append((start, end))
            candidates.append((start, end, replacements[original]))
    return sorted(candidates, reverse=True)


def _replace_text(text: str, replacements: dict[str, str]) -> tuple[str, int]:
    matches = _matches(text, replacements)
    for start, end, placeholder in matches:
        text = text[:start] + placeholder + text[end:]
    return text, len(matches)


def _replace_nodes(nodes: Sequence[etree._Element], replacements: dict[str, str]) -> int:
    original_parts = [node.text or "" for node in nodes]
    combined = "".join(original_parts)
    matches = _matches(combined, replacements)
    if not matches:
        return 0
    starts: list[int] = []
    cursor = 0
    for part in original_parts:
        starts.append(cursor)
        cursor += len(part)
    for start, end, placeholder in matches:
        start_index = max(index for index, node_start in enumerate(starts) if node_start <= start)
        end_index = max(index for index, node_start in enumerate(starts) if node_start < end)
        local_start = start - starts[start_index]
        local_end = end - starts[end_index]
        if start_index == end_index:
            current = nodes[start_index].text or ""
            nodes[start_index].text = current[:local_start] + placeholder + current[local_end:]
            continue
        start_text = nodes[start_index].text or ""
        end_text = nodes[end_index].text or ""
        nodes[start_index].text = start_text[:local_start] + placeholder
        for index in range(start_index + 1, end_index):
            nodes[index].text = ""
        nodes[end_index].text = end_text[local_end:]
    return len(matches)


def _transform_office(content: bytes, extension: str, replacements: dict[str, str]) -> tuple[bytes, int]:
    source = io.BytesIO(content)
    target = io.BytesIO()
    total = 0
    try:
        with zipfile.ZipFile(source) as input_package, zipfile.ZipFile(target, "w") as output_package:
            for info in input_package.infolist():
                data = input_package.read(info.filename)
                if _office_member_supported(extension, info.filename):
                    root = etree.fromstring(data)
                    changed = 0
                    for nodes in _text_groups(root, extension):
                        changed += _replace_nodes(nodes, replacements)
                    if changed:
                        data = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=None)
                        total += changed
                output_package.writestr(info, data)
    except (zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        raise ValueError("Файл Office повреждён или защищён паролем.") from exc
    return target.getvalue(), total


def _replace_eml_message(message: Message, replacements: dict[str, str]) -> int:
    total = 0
    for header in ("from", "to", "cc", "bcc", "reply-to", "subject"):
        values = message.get_all(header, [])
        if not values:
            continue
        del message[header]
        for value in values:
            updated, count = _replace_text(str(value), replacements)
            message[header] = updated
            total += count
    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_maintype() == "text":
            try:
                current = part.get_content()
            except (LookupError, UnicodeDecodeError):
                continue
            updated, count = _replace_text(current, replacements)
            if count:
                subtype = part.get_content_subtype()
                charset = part.get_content_charset() or "utf-8"
                part.set_content(updated, subtype=subtype, charset=charset)
                total += count
            continue
        filename = part.get_filename()
        extension = Path(filename).suffix.lower() if filename else ""
        if extension not in {".docx", ".xlsx", ".pptx"}:
            continue
        payload = part.get_payload(decode=True) or b""
        updated, count = _transform_office(payload, extension, replacements)
        if count:
            part.set_payload(base64.b64encode(updated).decode("ascii"))
            if part.get("Content-Transfer-Encoding"):
                part.replace_header("Content-Transfer-Encoding", "base64")
            else:
                part["Content-Transfer-Encoding"] = "base64"
            total += count
    return total


def _transform_document(content: bytes, filename: str, replacements: dict[str, str]) -> tuple[bytes, int]:
    extension = _extension(filename)
    if extension != ".eml":
        return _transform_office(content, extension, replacements)
    message = BytesParser(policy=policy.default).parsebytes(content)
    count = _replace_eml_message(message, replacements)
    return message.as_bytes(policy=policy.default), count


def _derive_key(password: str, salt: bytes, parameters: dict | None = None) -> bytes:
    if len(password) < 10:
        raise ValueError("Пароль ключа должен содержать не менее 10 символов.")
    parameters = parameters or {"iterations": 3, "lanes": 4, "memory_cost": 64 * 1024}
    kdf = Argon2id(
        salt=salt,
        length=32,
        iterations=int(parameters["iterations"]),
        lanes=int(parameters["lanes"]),
        memory_cost=int(parameters["memory_cost"]),
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _validated_kdf_parameters(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("Параметры шифрования ключа повреждены.")
    try:
        parameters = {
            "iterations": int(value["iterations"]),
            "lanes": int(value["lanes"]),
            "memory_cost": int(value["memory_cost"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Параметры шифрования ключа повреждены.") from exc
    if not 1 <= parameters["iterations"] <= 10:
        raise ValueError("Недопустимые параметры шифрования ключа.")
    if not 1 <= parameters["lanes"] <= 8:
        raise ValueError("Недопустимые параметры шифрования ключа.")
    if not 8 * 1024 <= parameters["memory_cost"] <= 256 * 1024:
        raise ValueError("Недопустимые параметры шифрования ключа.")
    return parameters


def _encrypted_key(payload: dict, password: str) -> bytes:
    salt = os.urandom(16)
    parameters = {"iterations": 3, "lanes": 4, "memory_cost": 64 * 1024}
    token = Fernet(_derive_key(password, salt, parameters)).encrypt(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    envelope = {
        "format": KEY_FORMAT,
        "version": KEY_VERSION,
        "kdf": "argon2id",
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "parameters": parameters,
        "payload": token.decode("ascii"),
    }
    return json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8")


def _decrypt_key(content: bytes, password: str) -> dict:
    try:
        envelope = json.loads(content.decode("utf-8"))
        if envelope.get("format") != KEY_FORMAT or envelope.get("version") != KEY_VERSION:
            raise ValueError("Это не ключ обезличивания Atlas или его версия не поддерживается.")
        salt = base64.urlsafe_b64decode(envelope["salt"])
        if len(salt) != 16:
            raise ValueError("Ключ обезличивания содержит повреждённую соль.")
        parameters = _validated_kdf_parameters(envelope["parameters"])
        key = _derive_key(password, salt, parameters)
        decrypted = Fernet(key).decrypt(envelope["payload"].encode("ascii"))
        return json.loads(decrypted.decode("utf-8"))
    except InvalidToken as exc:
        raise ValueError("Неверный пароль или ключ был повреждён.") from exc
    except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("Это не ключ", "Пароль ключа")):
            raise
        raise ValueError("Ключ обезличивания повреждён или имеет неизвестный формат.") from exc


def anonymize_document(
    content: bytes,
    filename: str,
    findings: Sequence[Finding],
    password: str,
) -> AnonymizedResult:
    extension = _extension(filename)
    all_text = "\n".join(extract_texts(content, filename))
    counters: dict[str, int] = {}
    replacements: dict[str, str] = {}
    categories: dict[str, str] = {}
    for finding in findings:
        if finding.value in replacements:
            continue
        counters[finding.category] = counters.get(finding.category, 0) + 1
        placeholder = (
            f"atlas-email-{counters[finding.category]:04d}@anonymous.invalid"
            if finding.category == "EMAIL"
            else f"ATLAS-{finding.category}-{counters[finding.category]:04d}"
        )
        while placeholder in all_text:
            counters[finding.category] += 1
            placeholder = (
                f"atlas-email-{counters[finding.category]:04d}@anonymous.invalid"
                if finding.category == "EMAIL"
                else f"ATLAS-{finding.category}-{counters[finding.category]:04d}"
            )
        replacements[finding.value] = placeholder
        categories[placeholder] = finding.category
    transformed, count = _transform_document(content, filename, replacements)
    if count == 0:
        raise ValueError("Выбранные значения не найдены в документе.")
    stem = Path(filename).stem
    anonymous_name = f"{stem}_anonymous{extension}"
    key_name = f"{stem}_anonymous.atlas-key.json"
    key_payload = {
        "source_filename": Path(filename).name,
        "source_extension": extension,
        "anonymous_filename": anonymous_name,
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "anonymous_sha256": hashlib.sha256(transformed).hexdigest(),
        "mapping": {placeholder: original for original, placeholder in replacements.items()},
        "categories": categories,
    }
    return AnonymizedResult(
        filename=anonymous_name,
        content=transformed,
        key_filename=key_name,
        key_content=_encrypted_key(key_payload, password),
        replacements=count,
    )


def restore_document(
    content: bytes,
    filename: str,
    key_content: bytes,
    password: str,
) -> RestoredResult:
    payload = _decrypt_key(key_content, password)
    extension = _extension(filename)
    if extension != payload.get("source_extension"):
        raise ValueError("Расширение обрабатываемого файла не совпадает с ключом.")
    mapping = payload.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("В ключе нет таблицы восстановления.")
    restored, count = _transform_document(content, filename, mapping)
    if count == 0:
        raise ValueError("В файле не найдены метки из выбранного ключа.")
    source_name = Path(payload.get("source_filename") or filename).name
    output_name = f"{Path(source_name).stem}_restored{extension}"
    return RestoredResult(output_name, restored, count)


def safe_output_directory(base: Path, subfolder: str) -> Path:
    raw_parts = [part for part in re.split(r"[\\/]", subfolder.strip()) if part]
    if any(part in {".", ".."} or re.search(r"[<>:\"|?*]", part) for part in raw_parts):
        raise ValueError("Недопустимое имя папки.")
    clean_parts = raw_parts or ["exports"]
    target = base.joinpath(*clean_parts).resolve()
    root = base.resolve()
    if target != root and root not in target.parents:
        raise ValueError("Папка должна находиться внутри каталога экспорта Atlas.")
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_result(base: Path, subfolder: str, files: dict[str, bytes]) -> list[Path]:
    target = safe_output_directory(base, subfolder)
    prepared = [(target / Path(filename).name, content) for filename, content in files.items()]
    existing = [path.name for path, _ in prepared if path.exists()]
    if existing:
        raise FileExistsError(
            "Файл уже существует: " + ", ".join(existing) + ". Выберите другую папку сохранения."
        )
    paths = []
    for path, content in prepared:
        path.write_bytes(content)
        paths.append(path)
    return paths
