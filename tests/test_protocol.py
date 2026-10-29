"""Protocol lifecycle tests without touching the host network or running tinyvpn."""
import ast
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / 'luci-proto-tinyfecvpn/root/lib/netifd/proto/tinyfecvpn.sh'

MOCKS = r'''
record() {
    local item
    printf '%s' "$1"; shift
    for item in "$@"; do printf '\t%s' "$item"; done
    printf '\n'
}
function [ {
    if builtin [ "$#" -eq 3 ]; then
        if builtin [ "$1" = -x ] && builtin [ "$2" = /usr/bin/tinyvpn ]; then
            builtin [ "${CFG_missing_binary:-0}" != 1 ]; return
        fi
        if builtin [ "$1" = -c ] && builtin [ "$2" = /dev/net/tun ]; then
            builtin [ "${CFG_missing_tun:-0}" != 1 ]; return
        fi
    fi
    builtin [ "$@"
}
json_get_vars() {
    local name source
    for name in "$@"; do source="CFG_$name"; printf -v "$name" '%s' "${!source}"; done
}
json_get_values() { local source="CFG_$2"; printf -v "$1" '%s' "${!source}"; }
config_load() { :; }
config_foreach() { [ "${CFG_other:-0}" = 0 ] || "$1" other; }
config_get() { local source="OTHER_$3"; printf -v "$1" '%s' "${!source:-${4-}}"; }
resolveip() { [ "${CFG_dns_failure:-0}" = 0 ] || return 1; printf '198.51.100.10\n198.51.100.11\n'; }
proto_notify_error() { record error "$@"; }
proto_block_restart() { record block "$@"; }
proto_setup_failed() { record failed "$@"; }
proto_add_host_dependency() { record dependency "$@"; }
proto_run_command() { record command "$@"; started=1; [ "${CFG_run_failure:-0}" = 0 ]; }
proto_kill_command() { record kill "$@"; }
proto_init_update() { record update "$@"; }
proto_add_ipv4_address() { record address "$@"; }
proto_add_ipv4_route() { record route "$@"; }
proto_add_dns_server() { record dns "$@"; }
proto_send_update() { record send "$@"; [ "${CFG_update_failure:-0}" = 0 ]; }
sleep() { record sleep "$@"; }
ip() {
    if [ "$1 $2" = 'link show' ]; then
        [ "${CFG_busy:-0}" = 0 ] || return 0
        [ "${started:-0}" = 1 ] && [ "${CFG_timeout:-0}" = 0 ]; return
    fi
    if [ "$1 $2" = 'link set' ]; then record mtu "$@"; [ "${CFG_mtu_failure:-0}" = 0 ]; return; fi
    record unexpected "$@"; return 99
}
'''


class ProtocolTest(unittest.TestCase):
    def setup_protocol(self, **values):
        env = {k: v for k, v in os.environ.items()
               if k not in ('BASH_ENV', 'ENV') and not k.startswith('BASH_FUNC_')}
        env.update(INCLUDE_ONLY='1', LC_ALL='C', CFG_server='vpn.example', CFG_tunlink='wan')
        env.update({('OTHER_' + k[6:] if k.startswith('other_') else 'CFG_' + k): str(v)
                    for k, v in values.items()})
        result = subprocess.run(['bash', '-c', '. "$1"\n' + MOCKS + '\nproto_tinyfecvpn_setup vpn0',
                                 'protocol-test', str(SHELL)], env=env, universal_newlines=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        self.assertEqual(result.stderr, '')
        return result.returncode, [line.split('\t') for line in result.stdout.splitlines()]

    def test_client_uses_one_resolved_address_and_netifd_owns_address(self):
        code, events = self.setup_protocol()
        self.assertEqual(code, 0)
        self.assertIn(['dependency', 'vpn0', '198.51.100.10', 'wan'], events)
        command = next(e[2:] for e in events if e[0] == 'command')
        self.assertEqual(command[command.index('-r') + 1], '198.51.100.10:4096')
        self.assertNotIn('198.51.100.11:4096', command)
        self.assertIn('--keep-reconnect', command)
        self.assertNotIn('--persist-tun', command)
        self.assertNotIn('--report', command)  # --report 0 is rejected upstream
        self.assertTrue(command[command.index('--manual-set-tun') + 1].startswith('-'))
        self.assertEqual(command[command.index('--log-level') + 1], '3')
        self.assertIn(['update', 'tfec-vpn0', '1'], events)  # address-external must be absent
        self.assertIn(['address', '10.22.22.2', '32', '', '10.22.22.1'], events)
        self.assertFalse(any(e[0] == 'route' for e in events))

    def test_server_point_to_point_and_no_client_flags(self):
        code, events = self.setup_protocol(role='server', subnet='10.50.0.0')
        self.assertEqual(code, 0)
        command = next(e[2:] for e in events if e[0] == 'command')
        self.assertIn('-s', command)
        self.assertIn('0.0.0.0:4096', command)
        self.assertNotIn('--keep-reconnect', command)
        self.assertNotIn('-r', command)
        self.assertIn(['address', '10.50.0.1', '32', '', '10.50.0.2'], events)

    def test_default_route_is_explicit_and_can_be_disabled(self):
        for values, wanted in [({}, False), ({'defaultroute': 1}, False),
                               ({'gateway': '10.22.22.1'}, True),
                               ({'gateway': '10.22.22.1', 'defaultroute': 0}, False)]:
            with self.subTest(values=values):
                code, events = self.setup_protocol(**values)
                self.assertEqual(code, 0)
                self.assertEqual(['route', '0.0.0.0', '0', '10.22.22.1'] in events, wanted)

    def test_key_remains_one_literal_argument(self):
        key = 'literal;$(touch${IFS}/tmp/must-not-exist)`id`'
        code, events = self.setup_protocol(key=key, keep_reconnect=0, report=10, log_level=4)
        self.assertEqual(code, 0)
        command = next(e[2:] for e in events if e[0] == 'command')
        self.assertIn('-k' + key, command)
        self.assertEqual(command[command.index('--report') + 1], '10')
        self.assertNotIn('--keep-reconnect', command)

    def test_key_cannot_trigger_upstream_argument_prescan(self):
        code, events = self.setup_protocol(key='--unit-test')
        self.assertEqual(code, 0)
        command = next(e[2:] for e in events if e[0] == 'command')
        self.assertIn('-k--unit-test', command)
        self.assertNotIn('--unit-test', command)

    def test_dns_is_published(self):
        code, events = self.setup_protocol(dns='1.1.1.1 2606:4700:4700::1111')
        self.assertEqual(code, 0)
        self.assertIn(['dns', '1.1.1.1'], events)
        self.assertIn(['dns', '2606:4700:4700::1111'], events)

    def test_invalid_configuration_never_starts_process(self):
        cases = [({'role': 'other'}, 'INVALID_ROLE'), ({'server': 'bad;host'}, 'INVALID_SERVER'),
                 ({'server_port': 65536}, 'INVALID_SERVER'), ({'subnet': '10.1.0.1'}, 'INVALID_SUBNET'),
                 ({'subnet': '224.1.0.0'}, 'INVALID_SUBNET'), ({'subnet': '10.01.0.0'}, 'INVALID_SUBNET'),
                 ({'tunname': 'x' * 16}, 'INVALID_DEVICE'), ({'tunlink': 'vpn0'}, 'INVALID_UPLINK'),
                 ({'key': 'two words'}, 'INVALID_KEY'), ({'key': 'x' * 129}, 'INVALID_KEY'),
                 ({'fec': '0:10'}, 'INVALID_CONFIG'), ({'fec': '200:56'}, 'INVALID_CONFIG'),
                 ({'fec': '10:3,5:1'}, 'INVALID_CONFIG'), ({'fec': '20:10,'}, 'INVALID_CONFIG'),
                 ({'fec_timeout': '08'}, 'INVALID_CONFIG'), ({'fec_mode': 1}, 'INVALID_MTU'),
                 ({'mssfix': 50}, 'INVALID_MSS'), ({'gateway': '10.22.22.9'}, 'INVALID_GATEWAY'),
                 ({'role': 'server', 'listen_addr': '::'}, 'INVALID_LISTEN'),
                 ({'missing_binary': 1}, 'MISSING_BINARY'), ({'missing_tun': 1}, 'MISSING_TUN')]
        for values, error in cases:
            with self.subTest(values=values):
                code, events = self.setup_protocol(**values)
                self.assertNotEqual(code, 0)
                self.assertIn(['error', 'vpn0', error], events)
                self.assertFalse(any(e[0] == 'command' for e in events))

    def test_valid_fec_and_mode_one(self):
        for fec in ('1:0', '255:0', '1:254', '1:3,2:4,10:6,20:10'):
            with self.subTest(fec=fec):
                code, _ = self.setup_protocol(fec=fec, fec_mode=1, tun_mtu=1200)
                self.assertEqual(code, 0)

    def test_configured_instance_conflicts(self):
        base = dict(other=1, other_proto='tinyfecvpn', other_subnet='10.99.0.0')
        for values, error in [({'other_tunname': 'tfec-vpn0'}, 'DEVICE_IN_USE'),
                              ({'other_subnet': '10.22.22.0'}, 'SUBNET_IN_USE'),
                              ({'role': 'server', 'other_role': 'server'}, 'PORT_IN_USE')]:
            with self.subTest(values=values):
                code, events = self.setup_protocol(**dict(base, **values))
                self.assertNotEqual(code, 0)
                self.assertIn(['error', 'vpn0', error], events)
        code, _ = self.setup_protocol(**dict(base, role='server', listen_addr='192.0.2.1',
                                             other_role='server', other_listen_addr='192.0.2.2'))
        self.assertEqual(code, 0)

    def test_busy_device_is_not_taken_over(self):
        code, events = self.setup_protocol(busy=1)
        self.assertNotEqual(code, 0)
        self.assertIn(['error', 'vpn0', 'DEVICE_IN_USE'], events)
        self.assertFalse(any(e[0] in ('command', 'mtu') for e in events))

    def test_transient_and_startup_failures(self):
        for fault in ('dns_failure', 'run_failure', 'timeout', 'mtu_failure', 'update_failure'):
            with self.subTest(fault=fault):
                code, events = self.setup_protocol(**{fault: 1}, startup_timeout=2)
                self.assertNotEqual(code, 0)
                self.assertIn(['failed', 'vpn0'], events)
                self.assertNotIn(['block', 'vpn0'], events)
                if fault in ('timeout', 'mtu_failure', 'update_failure'):
                    self.assertIn(['kill', 'vpn0'], events)
                if fault in ('dns_failure', 'run_failure', 'timeout', 'mtu_failure'):
                    self.assertNotIn(['send', 'vpn0'], events)

    def test_teardown(self):
        result = subprocess.run(['sh', '-c', '. "$1"; proto_kill_command() { printf "%s" "$1"; }; proto_tinyfecvpn_teardown vpn0',
                                 'teardown-test', str(SHELL)], env=dict(os.environ, INCLUDE_ONLY='1'),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'vpn0')


class ArtifactsTest(unittest.TestCase):
    def test_collection_filters_dependencies_and_checks_hashes(self):
        for extension, names in [('ipk', ['luci-proto-tinyfecvpn_1.0.0-1_all', 'luci-i18n-tinyfecvpn-zh-cn_1_all']),
                                 ('apk', ['luci-proto-tinyfecvpn-1.0.0-r1', 'luci-i18n-tinyfecvpn-zh-cn-1-r1'])]:
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as tmp:
                source, output = Path(tmp) / 'input', Path(tmp) / 'output'
                source.mkdir()
                for name in names + ['tinyfecvpn-20230206', 'libc-1']:
                    (source / (name + '.' + extension)).write_bytes(name.encode())
                result = subprocess.run(['bash', str(ROOT / 'scripts/collect-packages.sh'), str(source), str(output)],
                                        env=dict(os.environ, ARCH='x86_64', SDK='test'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(list(output.iterdir())), 3)
                for line in (output / 'sha256sums_openwrt-test').read_text(encoding='utf-8').splitlines():
                    digest, name = line.split('  ')
                    self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
                    self.assertTrue(name.startswith('luci-'))

    def test_missing_translation_fails_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'luci-proto-tinyfecvpn_1_all.ipk').touch()
            result = subprocess.run(['bash', str(ROOT / 'scripts/collect-packages.sh'), tmp, tmp + '/out'],
                                    env=dict(os.environ, ARCH='x86_64', SDK='test'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            self.assertNotEqual(result.returncode, 0)

    def test_frontend(self):
        result = subprocess.run(['node', str(ROOT / 'tests/frontend.js')], universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chinese_translation_covers_frontend(self):
        source = (ROOT / 'luci-proto-tinyfecvpn/htdocs/luci-static/resources/protocol/tinyfecvpn.js').read_text(encoding='utf-8')
        po = (ROOT / 'luci-proto-tinyfecvpn/po/zh_Hans/tinyfecvpn.po').read_text(encoding='utf-8')
        translations = {ast.literal_eval(a): ast.literal_eval(b)
                        for a, b in re.findall(r'^msgid (".*")\nmsgstr (".*")$', po, re.M)}
        for raw in re.findall(r"_\('((?:\\.|[^'\\])*)'\)", source):
            self.assertTrue(translations.get(raw), raw)


if __name__ == '__main__':
    unittest.main()
