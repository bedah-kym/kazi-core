"""Compose files must not publish infrastructure ports on all interfaces."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compose_ports"

ALLOWED_PUBLIC_PORTS = frozenset({"8000"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _short_form(entry):
    spec = str(entry).split("/", 1)[0]
    host_ip = ""
    if spec.startswith("["):
        end = spec.find("]")
        if end == -1:
            raise ValueError(f"unterminated IPv6 host in {entry!r}")
        host_ip = spec[1:end]
        spec = spec[end + 1:].lstrip(":")
    parts = spec.split(":")
    if len(parts) == 1:
        return host_ip, parts[0], parts[0]
    if len(parts) == 2:
        return host_ip, parts[0], parts[1]
    if len(parts) == 3:
        return parts[0] or host_ip, parts[1], parts[2]
    raise ValueError(f"unrecognized port specification {entry!r}")


def _long_form(entry):
    target = str(entry.get("target", ""))
    published = entry.get("published")
    host_port = target if published is None else str(published)
    return str(entry.get("host_ip") or ""), host_port, target


def public_port_violations(compose, source="<compose>"):
    violations = []
    services = compose.get("services") or {}
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        for entry in service.get("ports") or []:
            try:
                if isinstance(entry, dict):
                    host_ip, host_port, container_port = _long_form(entry)
                else:
                    host_ip, host_port, container_port = _short_form(entry)
            except ValueError as exc:
                violations.append(f"{source}: service {service_name!r}: {exc}")
                continue
            if host_ip in LOOPBACK_HOSTS:
                continue
            if host_port in ALLOWED_PUBLIC_PORTS:
                continue
            where = host_ip or "all interfaces"
            violations.append(
                f"{source}: service {service_name!r} publishes container port "
                f"{container_port} on {where} (host port {host_port})"
            )
    return violations


def _load(name):
    path = FIXTURES / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class PublicPortCheckerTest(unittest.TestCase):
    def test_short_forms_on_all_interfaces_are_violations(self):
        violations = public_port_violations(_load("public_short_forms.yml"), "short")
        self.assertEqual(len(violations), 4, violations)
        for name in ("plain", "wildcard-ipv4", "wildcard-ipv6", "bare"):
            self.assertTrue(
                any(f"service {name!r}" in violation for violation in violations),
                f"{name} not reported: {violations}",
            )

    def test_long_forms_on_all_interfaces_are_violations(self):
        violations = public_port_violations(_load("public_long_forms.yml"), "long")
        self.assertEqual(len(violations), 4, violations)
        for name in ("long-no-host", "long-wildcard-ipv4", "long-wildcard-ipv6", "long-target-only"):
            self.assertTrue(
                any(f"service {name!r}" in violation for violation in violations),
                f"{name} not reported: {violations}",
            )

    def test_loopback_unpublished_and_web_ports_pass(self):
        self.assertEqual(public_port_violations(_load("private_forms.yml"), "private"), [])

    def test_checker_fails_on_pre_change_main_ports(self):
        violations = public_port_violations(_load("main_before.yml"), "main-before")
        self.assertEqual(len(violations), 5, violations)
        for name in (
            "docker-compose-redis",
            "docker-compose-oci-redis",
            "temporal",
            "temporal-postgresql",
            "temporal-ui",
        ):
            self.assertTrue(
                any(f"service {name!r}" in violation for violation in violations),
                f"{name} not reported: {violations}",
            )


class RepoComposeFilesTest(unittest.TestCase):
    def test_every_root_compose_file_publishes_only_the_web_port(self):
        files = sorted(REPO_ROOT.glob("docker-compose*.yml"))
        self.assertGreaterEqual(len(files), 3, files)
        for path in files:
            with self.subTest(compose=path.name):
                compose = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertEqual(public_port_violations(compose, path.name), [])
