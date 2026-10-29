'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

String.prototype.format = function(...args) {
	let i = 0;
	return this.replace(/%[sd]/g, () => String(args[i++]));
};
let config = [], values = {}, options = {};
const section = {
	taboption(tab, type, name) {
		return options[name] = { enabled: '1', disabled: '0', value() {}, depends() {} };
	},
	formvalue(id, name) {
		return values[id] && Object.hasOwn(values[id], name) ? values[id][name]
			: id == 'vpn0' && options[name] ? options[name].default : undefined;
	}
};
const network = {
	registerPatternVirtual() {},
	registerProtocol(name, implementation) { assert.equal(name, 'tinyfecvpn'); return implementation; },
	getIfnameOf(value) { return value; }
};
const uci = {
	get(pkg, id, name) { return (config.find(c => c['.name'] == id) || {})[name]; },
	sections(pkg, type, callback) { config.forEach(callback); }
};
const file = path.join(__dirname, '../luci-proto-tinyfecvpn/htdocs/luci-static/resources/protocol/tinyfecvpn.js');
const protocol = new Function('form', 'network', 'uci', 'widgets', '_', fs.readFileSync(file, 'utf8'))(
	{}, network, uci, {}, value => value);
protocol.sid = 'vpn0';
protocol.renderFormOptions(section);

const valid = (name, value) => options[name].validate('vpn0', value) === true;
assert(valid('subnet', '10.22.22.0'));
for (const value of ['10.22.22.1', '224.0.0.0', '127.0.0.0', '10.01.0.0']) assert(!valid('subnet', value));
for (const value of ['20:10', '1:3,2:4,10:6,20:10', '255:0', '1:254']) assert(valid('fec', value));
for (const value of ['', '0:3', '250:6', '20:10,10:3', '2:3,', '2:03', '2:3;id']) assert(!valid('fec', value));
assert(valid('key', 'literal;$(id)'));
assert(!valid('key', 'two words'));
assert(!valid('key', '密'.repeat(43)));
assert(valid('tunname', 'tun0'));
assert(!valid('tunname', 'x'.repeat(16)));
assert(!valid('tunname', 'tun;id'));
assert(!valid('tunlink', 'vpn0'));
assert(valid('gateway', ''));
assert(valid('gateway', '10.22.22.1'));
assert(!valid('gateway', '192.0.2.1'));
assert(!valid('server_port', '04096'));
assert(!valid('mssfix', '50'));
assert(valid('mssfix', '0'));
values.vpn0 = { fec_mode: '1', fec_mtu: '1250' };
assert(!valid('tun_mtu', '1500'));
assert(valid('tun_mtu', '1200'));
config = [{ '.name': 'other', proto: 'tinyfecvpn', subnet: '10.22.22.0', tunname: 'tun0', role: 'server' }];
assert(!valid('subnet', '10.22.22.0'));
assert(!valid('tunname', 'tun0'));
assert(!valid('listen_port', '4096'));
values.other = { subnet: '10.99.0.0', tunname: 'tun1', listen_port: '4097' };
assert(valid('subnet', '10.22.22.0')); // unsaved edits also participate in conflict checks
assert(valid('tunname', 'tun0'));
assert(valid('listen_port', '4096'));
protocol._ubus = () => null;
protocol.get = name => name == 'tunname' ? 'custom0' : null;
assert.equal(protocol.getIfname(), 'custom0');
assert(protocol.containsDevice('custom0'));
assert(!protocol.containsDevice('other0'));
