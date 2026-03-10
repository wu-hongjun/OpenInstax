#!/usr/bin/env python3

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SWIFT_ROOT = ROOT / "macos" / "InstantLink"
RESOURCES_ROOT = ROOT / "macos" / "Resources"
ENGLISH_FILE = RESOURCES_ROOT / "en.lproj" / "Localizable.strings"

SWIFT_KEY_PATTERN = re.compile(r'L\("([^"]+)"')
STRINGS_KEY_PATTERN = re.compile(r'^"((?:\\.|[^"])*)"\s*=\s*"((?:\\.|[^"])*)";', re.MULTILINE)


def load_strings(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    return {match.group(1): match.group(2) for match in STRINGS_KEY_PATTERN.finditer(text)}


def collect_swift_keys() -> set[str]:
    keys: set[str] = set()
    for swift_file in SWIFT_ROOT.rglob("*.swift"):
        text = swift_file.read_text(encoding="utf-8")
        keys.update(SWIFT_KEY_PATTERN.findall(text))
    return keys


def main() -> int:
    errors: list[str] = []

    code_keys = collect_swift_keys()
    english_keys = set(load_strings(ENGLISH_FILE))

    missing_from_english = sorted(code_keys - english_keys)
    if missing_from_english:
        errors.append(
            "Keys used in Swift but missing from en.lproj:\n  - "
            + "\n  - ".join(missing_from_english)
        )

    for locale_file in sorted(RESOURCES_ROOT.glob("*.lproj/Localizable.strings")):
        locale_keys = set(load_strings(locale_file))
        missing = sorted(english_keys - locale_keys)
        extra = sorted(locale_keys - english_keys)

        if missing:
            errors.append(
                f"{locale_file.parent.name} is missing keys:\n  - " + "\n  - ".join(missing)
            )
        if extra:
            errors.append(
                f"{locale_file.parent.name} has extra keys not in en.lproj:\n  - "
                + "\n  - ".join(extra)
            )

    if errors:
        print("\n\n".join(errors))
        return 1

    print("Localization keys are in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
