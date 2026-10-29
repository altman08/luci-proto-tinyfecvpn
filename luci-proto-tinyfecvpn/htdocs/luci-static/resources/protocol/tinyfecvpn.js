'use strict';
'require form';
'require network';
'require uci';
'require tools.widgets as widgets';

function ipv4(value) {
	const parts = (value || '').split('.');
	return parts.length == 4 && parts.every(function(part) {
		return /^(0|[1-9][0-9]{0,2})$/.test(part) && Number(part) <= 255;
	});
}

network.registerPatternVirtual(/^tfec-.+$/);

return network.registerProtocol('tinyfecvpn', {
	getI18n() { return _('tinyfecVPN'); },
	getIfname() { return this._ubus('l3_device') || this.get('tunname') || 'tfec-%s'.format(this.sid); },
	getPackageName() { return 'luci-proto-tinyfecvpn'; },
	isFloating() { return true; },
	isVirtual() { return true; },
	getDevices() { return null; },
	containsDevice(ifname) { return network.getIfnameOf(ifname) == this.getIfname(); },

	renderFormOptions(s) {
		let o;
		const get = function(id, field, fallback) {
			const value = s.formvalue(id, field);
			return (value == null ? uci.get('network', id, field) : value) || fallback;
		};
		const conflict = function(id, predicate) {
			let found = false;
			uci.sections('network', 'interface', function(section) {
				if (section['.name'] != id && section.proto == 'tinyfecvpn' && predicate(section['.name']))
					found = true;
			});
			return found;
		};
		const number = function(name, title, value, min, max, description, tab) {
			const field = s.taboption(tab || 'advanced', form.Value, name, title, description);
			field.default = String(value);
			field.rmempty = false;
			field.datatype = 'range(%d,%d)'.format(min, max);
			field.validate = function(id, input) {
				return /^(0|[1-9][0-9]*)$/.test(input || '') && Number(input) >= min && Number(input) <= max
					? true : _('Enter an integer from %d to %d without leading zeros.').format(min, max);
			};
			return field;
		};
		const listenConflict = function(id, addr, port) {
			return conflict(id, function(other) {
				const otherAddr = get(other, 'listen_addr', '0.0.0.0');
				return get(other, 'role', 'client') == 'server' && get(other, 'listen_port', '4096') == port &&
					(addr == otherAddr || addr == '0.0.0.0' || otherAddr == '0.0.0.0');
			}) ? _('This UDP listen address and port overlap another tinyfecVPN server.') : true;
		};

		o = s.taboption('general', form.ListValue, 'role', _('Role'),
			_('Interface up means the local TUN is ready. It does not confirm that the remote peer is reachable. Each server instance supports one client.'));
		o.value('client', _('Client'));
		o.value('server', _('Server'));
		o.default = 'client';
		o.rmempty = false;

		o = s.taboption('general', form.Value, 'server', _('Server address'), _('IPv4 address or hostname of the remote server.'));
		o.depends('role', 'client');
		o.rmempty = false;
		o.validate = function(id, value) {
			const host = value || '';
			const valid = /^[0-9.]+$/.test(host) ? ipv4(host) : host.length > 0 && host.length <= 253 &&
				host.split('.').every(label => /^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(label));
			return valid
				? true : _('Enter an IPv4 address or hostname without a port.');
		};

		o = number('server_port', _('Server UDP port'), 4096, 1, 65535, null, 'general');
		o.depends('role', 'client');

		o = s.taboption('general', form.Value, 'listen_addr', _('Listen address'));
		o.default = '0.0.0.0';
		o.rmempty = false;
		o.depends('role', 'server');
		o.validate = function(id, value) {
			return ipv4(value) ? listenConflict(id, value, get(id, 'listen_port', '4096')) : _('Enter an IPv4 address.');
		};

		o = number('listen_port', _('Listen UDP port'), 4096, 1, 65535, null, 'general');
		o.depends('role', 'server');
		const portValidate = o.validate;
		o.validate = function(id, value) {
			const valid = portValidate.call(this, id, value);
			return valid === true ? listenConflict(id, get(id, 'listen_addr', '0.0.0.0'), value) : valid;
		};

		o = s.taboption('general', form.Value, 'subnet', _('VPN subnet'),
			_('Use an IPv4 network ending in .0, such as 10.22.22.0. The server uses .1 and the client .2 as point-to-point addresses. Both ends must use the same subnet; local instances must use different subnets.'));
		o.default = '10.22.22.0';
		o.rmempty = false;
		o.validate = function(id, value) {
			const first = Number((value || '').split('.')[0]);
			if (!ipv4(value) || !value.endsWith('.0') || first == 0 || first == 127 || first >= 224)
				return _('Enter a unicast IPv4 network ending in .0.');
			return conflict(id, other => get(other, 'subnet', '10.22.22.0') == value)
				? _('This VPN subnet is already used by another tinyfecVPN interface.') : true;
		};

		o = s.taboption('general', form.Value, 'key', _('XOR key'),
			_('Optional simple XOR obfuscation, not authenticated encryption. Use the same key on both ends.'));
		o.password = true;
		o.validate = function(id, value) {
			return !value || (!/\s/.test(value) && new TextEncoder().encode(value).length <= 128)
				? true : _('The key must be at most 128 bytes and contain no whitespace.');
		};

		o = s.taboption('general', form.Value, 'fec', _('FEC redundancy'),
			_('For example 20:10 sends 10 recovery packets for 20 data packets. Also accepts 1:3,2:4,10:6,20:10. Data counts must increase and each data/recovery sum must be at most 255.'));
		o.default = '20:10';
		o.rmempty = false;
		o.validate = function(id, value) {
			let previous = 0;
			return value && value.split(',').every(function(group) {
				const match = /^([1-9][0-9]*):(0|[1-9][0-9]*)$/.exec(group);
				if (!match) return false;
				const data = Number(match[1]), recovery = Number(match[2]);
				const valid = data > previous && data + recovery <= 255;
				previous = data;
				return valid;
			}) ? true : _('Enter increasing x:y pairs with x >= 1, y >= 0 and x + y <= 255.');
		};

		o = s.taboption('general', form.Value, 'gateway', _('IPv4 gateway'),
			_('Optional: enter the server VPN address (.1) to request a default route. Leave empty for a tunnel to the peer or remote LANs.'));
		o.depends('role', 'client');
		o.validate = function(id, value) {
			return !value || value == get(id, 'subnet', '10.22.22.0').replace(/\.0$/, '.1')
				? true : _('The gateway must be the server VPN address (.1).');
		};

		o = s.taboption('advanced', form.ListValue, 'fec_mode', _('FEC mode'));
		o.value('0', _('0: lower bandwidth, packet splitting'));
		o.value('1', _('1: lower latency, no packet splitting'));
		o.default = '0';
		o.rmempty = false;

		number('fec_timeout', _('FEC queue timeout (ms)'), 8, 0, 1000);
		number('fec_mtu', _('FEC MTU'), 1250, 100, 1500,
			_('Maximum FEC packet payload before outer headers. This differs from the TUN MTU.'));
		o = number('tun_mtu', _('TUN MTU'), 1500, 576, 1500,
			_('In FEC mode 1, the TUN MTU must not exceed the FEC MTU.'));
		const mtuValidate = o.validate;
		o.validate = function(id, value) {
			const valid = mtuValidate.call(this, id, value);
			if (valid !== true) return valid;
			return get(id, 'fec_mode', '0') != '1' || Number(value) <= Number(get(id, 'fec_mtu', '1250'))
				? true : _('In mode 1, reduce the TUN MTU to the FEC MTU or less.');
		};
		o = number('mssfix', _('MSS adjustment size'), 1250, 0, 1500,
			_('Upstream derives the TCP MSS limit from this packet size. Zero disables adjustment.'));
		const mssValidate = o.validate;
		o.validate = function(id, value) {
			const valid = mssValidate.call(this, id, value);
			return valid !== true ? valid : (value == '0' || Number(value) >= 100 ? true : _('Use zero or a value from 100 to 1500.'));
		};

		o = s.taboption('advanced', form.Value, 'tunname', _('TUN device name'));
		o.placeholder = 'tfec-%s'.format(this.sid);
		o.validate = function(id, value) {
			const name = value || 'tfec-%s'.format(id);
			if (!/^[a-zA-Z0-9_-]{1,15}$/.test(name))
				return _('Use 1 to 15 letters, digits, underscores or hyphens. Set a shorter device name if needed.');
			return conflict(id, other => get(other, 'tunname', 'tfec-%s'.format(other)) == name)
				? _('This TUN device name is already used by another tinyfecVPN interface.') : true;
		};

		o = s.taboption('advanced', widgets.NetworkSelect, 'tunlink', _('Underlying interface'),
			_('Optional uplink used to reach the server or listen on the local address.'));
		o.nocreate = true;
		o.exclude = this.sid;
		o.validate = (id, value) => value != id ? true : _('The underlying interface cannot be this VPN interface.');

		o = s.taboption('advanced', form.Flag, 'keep_reconnect', _('Keep reconnecting'));
		o.default = o.enabled;
		o.rmempty = false;
		o.depends('role', 'client');

		number('startup_timeout', _('TUN startup timeout (seconds)'), 10, 1, 60);
		number('report', _('Traffic report interval (seconds)'), 0, 0, 3600,
			_('Zero disables reports. Reports require Info logging or higher, which also logs the XOR key.'));
		o = s.taboption('advanced', form.ListValue, 'log_level', _('Log level'),
			_('Info, Debug and Trace log the full command line including the XOR key. Warning is recommended. The key is also visible in process arguments.'));
		o.value('0', _('None'));
		o.value('1', _('Fatal'));
		o.value('2', _('Error'));
		o.value('3', _('Warning'));
		o.value('4', _('Info (includes key)'));
		o.value('5', _('Debug (includes key)'));
		o.value('6', _('Trace (includes key)'));
		o.default = '3';
		o.rmempty = false;
	}
});
