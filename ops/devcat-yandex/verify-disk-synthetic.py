#!/usr/bin/env python3
"""Review-gated synthetic Disk canary on an isolated DevCat MCP process."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import tomllib
import urllib.request
import uuid

from importlib.util import module_from_spec, spec_from_file_location

_spec = spec_from_file_location("verify_synthetic", Path(__file__).with_name("verify-synthetic.py"))
_common = module_from_spec(_spec)
_spec.loader.exec_module(_common)
Mcp = _common.Mcp
service_environment = _common.service_environment
ACCOUNT = "dedts"


def find_path(value, name):
    if isinstance(value, dict):
        if value.get("name") == name and isinstance(value.get("path"), str):
            return value["path"]
        for child in value.values():
            found = find_path(child, name)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_path(child, name)
            if found:
                return found
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    binary = Path(args.binary).resolve(strict=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex[:10].upper()
    marker = "YACLI-723-DISK-" + suffix
    root = f"disk:/{marker}"
    report = {"state": "RUNNING", "account": ACCOUNT, "scope": "isolated_synthetic_disk",
              "synthetic_ids": {"root": root, "small_file": "small.bin", "copy": "copy.bin",
                                "moved": "moved.bin", "large_file": "large.bin"},
              "cases": [], "cleanup_errors": []}
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    process = None
    temp_config = None
    mcp = None
    upload_file = None
    download_file = None
    root_claimed = False

    def save():
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    def case(operation, **details):
        row = {"operation": operation, "status": "PASS", **details}
        report["cases"].append(row)
        save()
        print(json.dumps(row, sort_keys=True), flush=True)

    def reviewed(name, values):
        return mcp.reviewed(name, {"account": ACCOUNT, **values})

    try:
        env = {key: value for key, value in os.environ.items() if key in {
            "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        }}
        env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        env.update(service_environment())
        config_source = Path(env["YACLI_CONFIG_DIR"]) / "accounts.toml"
        config = tomllib.loads(config_source.read_text())
        upload_root = Path(env["YACLI_DEVCAT_UPLOAD_ROOTS"].split(":")[0])
        download_root = Path(env["YACLI_DEVCAT_ATTACHMENT_ROOT"])
        if not upload_root.is_dir() or not os.access(upload_root, os.W_OK):
            raise RuntimeError("UPLOAD_ROOT_UNAVAILABLE")
        if not download_root.is_dir() or not os.access(download_root, os.W_OK):
            raise RuntimeError("DOWNLOAD_ROOT_UNAVAILABLE")
        service_user = subprocess.check_output(
            ["systemctl", "show", "yacli-devcat.service", "-p", "User", "--value"], text=True
        ).strip()
        import getpass
        if service_user != getpass.getuser():
            raise RuntimeError("CANDIDATE_USER_MISMATCH")
        temp_config = tempfile.TemporaryDirectory(prefix="yacli-723-disk-candidate-")
        os.chmod(temp_config.name, 0o700)
        shutil.copyfile(config_source, Path(temp_config.name) / "accounts.toml")
        os.chmod(Path(temp_config.name) / "accounts.toml", 0o600)
        env.update({"YACLI_CONFIG_DIR": temp_config.name, "YACLI_DEVCAT_MODE": "1",
                    "YACLI_DEVCAT_ACCOUNT": ACCOUNT,
                    "YACLI_DEVCAT_ALLOWED_ACCOUNTS": "dedts,ykkareliadom,dvabobra2014",
                    "YACLI_DEVCAT_CAPABILITIES": "disk.read,disk.write,disk.delete",
                    "YACLI_DEVCAT_DOWNLOAD_ROOTS": str(download_root),
                    "YACLI_DEVCAT_TRUST_SECURE_TUNNEL": "1",
                    "CREDENTIALS_DIRECTORY": "/run/credentials/yacli-devcat.service"})
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}"
        process = subprocess.Popen([str(binary), "mcp", "--transport", "http", "--listen", f"127.0.0.1:{port}"],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with urllib.request.urlopen(endpoint + "/healthz", timeout=0.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("CANDIDATE_HEALTH_FAILED")
        mcp = Mcp(endpoint)
        mcp.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                "clientInfo": {"name": "yacli-723-disk-synthetic", "version": "1"}})
        tools = mcp.rpc("tools/list", {}).get("result", {}).get("tools", [])
        by_name = {tool.get("name"): tool for tool in tools}
        required = {"yacli.disk.info", "yacli.disk.list", "yacli.disk.mkdir", "yacli.disk.upload",
                    "yacli.disk.copy", "yacli.disk.move", "yacli.disk.read", "yacli.disk.permissions",
                    "yacli.disk.download", "yacli.disk.trash.move", "yacli.disk.trash.list",
                    "yacli.disk.trash.restore"}
        if not required.issubset(by_name):
            raise RuntimeError("DISK_CATALOG_INCOMPLETE")
        risky = {name for name in required if name not in {"yacli.disk.info", "yacli.disk.list",
                 "yacli.disk.read", "yacli.disk.permissions", "yacli.disk.trash.list"}}
        if any(not {"dry_run", "review_sha256"}.issubset(by_name[name].get("inputSchema", {}).get("properties", {})) for name in risky):
            raise RuntimeError("DISK_REVIEW_SCHEMA_INCOMPLETE")
        case("disk.catalog+review-schema", tool_count=len(tools))
        mcp.call("disk.info", {"account": ACCOUNT})
        case("disk.info")
        root_listing = mcp.call("disk.list", {"account": ACCOUNT, "path": "disk:/", "limit": 1000, "offset": 0})
        if find_path(root_listing, marker):
            raise RuntimeError("DISK_NAME_COLLISION")
        root_claimed = True
        reviewed("disk.mkdir", {"path": root})
        case("disk.mkdir")
        fd, path = tempfile.mkstemp(prefix=marker + "-", suffix=".bin", dir=upload_root)
        upload_file = Path(path)
        small = b"YACLI-723 synthetic disk checksum\n"
        with os.fdopen(fd, "wb") as stream:
            stream.write(small)
        reviewed("disk.upload", {"source": str(upload_file), "path": f"{root}/small.bin"})
        case("disk.upload.small", bytes=len(small))
        reviewed("disk.copy", {"from_path": f"{root}/small.bin", "to_path": f"{root}/copy.bin"})
        case("disk.copy")
        reviewed("disk.move", {"from_path": f"{root}/copy.bin", "to_path": f"{root}/moved.bin"})
        case("disk.move")
        read = mcp.call("disk.read", {"account": ACCOUNT, "path": f"{root}/moved.bin"})
        if base64.b64decode(read.get("data", "")) != small:
            raise RuntimeError("DISK_READ_CHECKSUM_MISMATCH")
        permissions = mcp.call("disk.permissions", {"account": ACCOUNT, "path": f"{root}/moved.bin"})
        if permissions.get("public_access", {}).get("enabled") is not False:
            raise RuntimeError("SYNTHETIC_FILE_NOT_PRIVATE")
        case("disk.read+permissions", bytes=len(small), public_access=False)
        large = bytes((index * 31 + 7) % 256 for index in range(2 * 1024 * 1024))
        expected_sha = hashlib.sha256(large).hexdigest()
        upload_file.write_bytes(large)
        reviewed("disk.upload", {"source": str(upload_file), "path": f"{root}/large.bin"})
        case("disk.upload.large", bytes=len(large), sha256=expected_sha)
        fd, path = tempfile.mkstemp(prefix=marker + "-download-", suffix=".bin", dir=download_root)
        os.close(fd)
        Path(path).unlink()
        download_file = Path(path)
        reviewed("disk.download", {"path": f"{root}/large.bin", "output_path": str(download_file), "force": False})
        if hashlib.sha256(download_file.read_bytes()).hexdigest() != expected_sha:
            raise RuntimeError("DISK_DOWNLOAD_CHECKSUM_MISMATCH")
        case("disk.download.large", bytes=len(large), sha256=expected_sha)
        reviewed("disk.trash.move", {"path": f"{root}/moved.bin"})
        case("disk.trash.move")
        trash = mcp.call("disk.trash.list", {"account": ACCOUNT, "path": "trash:/", "limit": 1000, "offset": 0})
        trash_path = find_path(trash, "moved.bin")
        if not trash_path:
            raise RuntimeError("DISK_TEST_FILE_NOT_IN_TRASH")
        reviewed("disk.trash.restore", {"trash_path": trash_path})
        restored = mcp.call("disk.read", {"account": ACCOUNT, "path": f"{root}/moved.bin"})
        if base64.b64decode(restored.get("data", "")) != small:
            raise RuntimeError("DISK_RESTORE_CHECKSUM_MISMATCH")
        case("disk.trash.restore+checksum")
        report["state"] = "PASS"
        save()
    except Exception as error:
        report["state"] = "BLOCKED"
        report["failure"] = {"code": str(error)[:120], "operation": "disk.synthetic"}
        save()
        print(json.dumps({"state": "BLOCKED", **report["failure"]}, sort_keys=True), flush=True)
    finally:
        if upload_file:
            upload_file.unlink(missing_ok=True)
        if download_file:
            download_file.unlink(missing_ok=True)
        if mcp and root_claimed:
            try:
                listing = mcp.call("disk.list", {"account": ACCOUNT, "path": "disk:/", "limit": 1000, "offset": 0})
                if find_path(listing, marker):
                    reviewed("disk.trash.move", {"path": root})
                case("cleanup.disk_root_to_reversible_trash")
            except Exception:
                report["cleanup_errors"].append("disk_root_cleanup_unverified")
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            report["candidate_process_stopped"] = True
        if temp_config:
            temp_config.cleanup()
        if report["cleanup_errors"] and report["state"] == "PASS":
            report["state"] = "BLOCKED"
            report["failure"] = {"code": "CLEANUP_UNVERIFIED", "operation": "cleanup"}
        save()
    return 0 if report["state"] == "PASS" and not report["cleanup_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
