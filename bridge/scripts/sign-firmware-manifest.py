#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_ROOT / "src"))

from instantlink_bridge.update.signing import (  # noqa: E402
    FirmwareSignatureError,
    default_signature_path,
    key_id_for_public_key,
    load_json_object,
    load_private_key,
    public_key_text,
    sign_manifest,
    verify_manifest_file,
    write_json_object,
)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    if argv and argv[0] in {"generate-test-key", "sign", "verify"}:
        return parse_subcommand_args(argv)
    return parse_direct_sign_args(argv)


def parse_direct_sign_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sign an InstantLink Bridge firmware JSON manifest with Ed25519.",
    )
    add_signing_args(parser, positional_manifest=True)
    parser.set_defaults(command="sign-direct")
    return parser.parse_args(argv)


def parse_subcommand_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sign or verify Bridge firmware manifests")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate-test-key", help="generate an Ed25519 test keypair")
    generate.add_argument("--private-key", required=True, type=Path)
    generate.add_argument("--public-key", required=True, type=Path)

    sign = subparsers.add_parser("sign", help="sign a manifest JSON file")
    add_signing_args(sign, positional_manifest=False)

    verify = subparsers.add_parser("verify", help="verify a signed manifest JSON file")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--signature", required=True, type=Path)
    verify.add_argument("--public-key", required=True, type=Path)
    verify.add_argument("--key-id", required=True)
    return parser.parse_args(argv)


def add_signing_args(parser: argparse.ArgumentParser, *, positional_manifest: bool) -> None:
    if positional_manifest:
        parser.add_argument("manifest", type=Path, help="Manifest JSON file to sign")
    else:
        parser.add_argument(
            "--manifest",
            required=True,
            type=Path,
            help="Manifest JSON file to sign",
        )
    parser.add_argument(
        "--private-key",
        required=True,
        type=Path,
        help="Ed25519 private key file, PEM or raw base64url",
    )
    parser.add_argument(
        "--key-id",
        help="Firmware signing key id; defaults to ed25519-sha256:<public-key-digest>",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Signature JSON path; defaults beside the manifest",
    )
    parser.add_argument(
        "--private-key-pass-env",
        help="Environment variable containing an encrypted private-key password",
    )
    parser.add_argument(
        "--print-public-key",
        action="store_true",
        help="Print the public key as raw base64url after signing",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "generate-test-key":
        generate_key(args.private_key, args.public_key)
        return 0
    if args.command == "verify":
        try:
            verify_manifest_file(
                args.manifest,
                args.signature,
                {args.key_id: args.public_key.read_bytes()},
            )
        except FirmwareSignatureError as exc:
            raise SystemExit(str(exc)) from exc
        print('{"ok":true}')
        return 0

    password = os.environ.get(args.private_key_pass_env) if args.private_key_pass_env else None
    private_key = load_private_key(args.private_key, password=password)
    manifest = load_json_object(args.manifest)
    signature = sign_manifest(manifest, private_key, key_id=args.key_id)
    signature_path = args.output or default_signature_path(args.manifest)
    write_json_object(signature_path, signature)

    print(f"Signed {args.manifest} -> {signature_path} ({signature['key_id']})")
    if args.print_public_key:
        print(f"public_key={public_key_text(private_key)}")
        print(f"derived_key_id={key_id_for_public_key(private_key)}")
    return 0


def generate_key(private_key_path: Path, public_key_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    private_key = Ed25519PrivateKey.generate()
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )
    )
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
