#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Runtime libraries and variables are provided/consumed by netifd.
# shellcheck disable=SC1091,SC2034

[ -n "$INCLUDE_ONLY" ] || {
	. /lib/functions.sh
	. ../netifd-proto.sh
	init_proto "$@"
}

proto_tinyfecvpn_init_config() {
	no_device=1
	available=1
	proto_config_add_string role
	proto_config_add_string server
	proto_config_add_int server_port
	proto_config_add_string 'listen_addr:ip4addr'
	proto_config_add_int listen_port
	proto_config_add_string 'subnet:ip4addr'
	proto_config_add_string 'gateway:ip4addr'
	proto_config_add_array 'dns:list(ipaddr)'
	proto_config_add_string key
	proto_config_add_string fec
	proto_config_add_int fec_mode
	proto_config_add_int fec_timeout
	proto_config_add_int fec_mtu
	proto_config_add_int tun_mtu
	proto_config_add_int mssfix
	proto_config_add_int report
	proto_config_add_int log_level
	proto_config_add_int startup_timeout
	proto_config_add_string tunname
	proto_config_add_string tunlink
	proto_config_add_boolean keep_reconnect
}

tinyfecvpn_uint() {
	case "$1" in ''|*[!0-9]*|0?*) return 1 ;; esac
	[ ${#1} -le 5 ] && [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

tinyfecvpn_ipv4() {
	local address="$1" octet
	case "$address" in ''|*[!0-9.]*|.*|*.|*..*) return 1 ;; esac
	local IFS=.
	# Only digits and dots are allowed before splitting.
	# shellcheck disable=SC2086
	set -- $address
	[ "$#" = 4 ] || return 1
	for octet in "$@"; do tinyfecvpn_uint "$octet" 0 255 || return 1; done
}

tinyfecvpn_fec() {
	local value="$1" group data redundant previous=0
	case "$value" in ''|*[!0-9,:]*|,*|*,|*,,*) return 1 ;; esac
	local IFS=,
	# shellcheck disable=SC2086
	set -- $value
	for group in "$@"; do
		data="${group%:*}"
		redundant="${group#*:}"
		tinyfecvpn_uint "$data" 1 255 && tinyfecvpn_uint "$redundant" 0 254 || return 1
		[ "$group" = "$data:$redundant" ] && [ "$data" -gt "$previous" ] &&
			[ $((data + redundant)) -le 255 ] || return 1
		previous="$data"
	done
}

tinyfecvpn_host() {
	local host="$1" label
	[ -n "$host" ] && [ ${#host} -le 253 ] || return 1
	case "$host" in
		*[!0-9.]*) ;;
		*) tinyfecvpn_ipv4 "$host"; return ;;
	esac
	case "$host" in *[!a-zA-Z0-9.-]*|.*|*.|*..*) return 1 ;; esac
	local IFS=.
	# shellcheck disable=SC2086
	set -- $host
	for label in "$@"; do
		case "$label" in ''|-*|*-) return 1 ;; esac
		[ ${#label} -le 63 ] || return 1
	done
}

tinyfecvpn_error() {
	proto_notify_error "$1" "$2"
	proto_block_restart "$1"
	return 1
}

# Check configured instances too: a stopped interface must not reserve the
# same device/address and fail unpredictably when it is started later.
tinyfecvpn_check_instance() {
	local section="$1" other_proto other_name other_subnet other_role other_addr other_port
	[ "$section" != "$config" ] || return 0
	config_get other_proto "$section" proto
	[ "$other_proto" = tinyfecvpn ] || return 0
	config_get other_name "$section" tunname "tfec-$section"
	config_get other_subnet "$section" subnet 10.22.22.0
	[ "$other_name" != "$tunname" ] || conflict=DEVICE_IN_USE
	[ "$other_subnet" != "$subnet" ] || conflict=SUBNET_IN_USE
	config_get other_role "$section" role client
	if [ "$role" = server ] && [ "$other_role" = server ]; then
		config_get other_addr "$section" listen_addr 0.0.0.0
		config_get other_port "$section" listen_port 4096
		if [ "$other_port" = "$listen_port" ] && {
			[ "$other_addr" = "$listen_addr" ] || [ "$other_addr" = 0.0.0.0 ] || [ "$listen_addr" = 0.0.0.0 ];
		}; then
			conflict=PORT_IN_USE
		fi
	fi
}

proto_tinyfecvpn_setup() {
	local config="$1"
	local LC_ALL=C
	local role server server_port listen_addr listen_port subnet key fec fec_mode fec_timeout
	local fec_mtu tun_mtu mssfix report log_level startup_timeout tunname tunlink keep_reconnect
	local defaultroute gateway dns addresses address local_ip peer_ip elapsed conflict
	json_get_vars role server server_port listen_addr listen_port subnet key fec fec_mode fec_timeout \
		fec_mtu tun_mtu mssfix report log_level startup_timeout tunname tunlink keep_reconnect defaultroute gateway
	json_get_values dns dns

	[ -x /usr/bin/tinyvpn ] || { tinyfecvpn_error "$config" MISSING_BINARY; return 1; }
	[ -c /dev/net/tun ] || { tinyfecvpn_error "$config" MISSING_TUN; return 1; }
	role="${role:-client}"
	server_port="${server_port:-4096}"
	listen_addr="${listen_addr:-0.0.0.0}"
	listen_port="${listen_port:-4096}"
	subnet="${subnet:-10.22.22.0}"
	fec="${fec:-20:10}"
	fec_mode="${fec_mode:-0}"
	fec_timeout="${fec_timeout:-8}"
	fec_mtu="${fec_mtu:-1250}"
	tun_mtu="${tun_mtu:-1500}"
	mssfix="${mssfix:-1250}"
	report="${report:-0}"
	log_level="${log_level:-3}"
	startup_timeout="${startup_timeout:-10}"
	tunname="${tunname:-tfec-$config}"

	case "$tunname" in ''|*[!a-zA-Z0-9_-]*) tinyfecvpn_error "$config" INVALID_DEVICE; return 1 ;; esac
	[ ${#tunname} -le 15 ] || { tinyfecvpn_error "$config" INVALID_DEVICE; return 1; }
	[ "$tunlink" != "$config" ] || { tinyfecvpn_error "$config" INVALID_UPLINK; return 1; }
	if ! tinyfecvpn_ipv4 "$subnet" || [ "${subnet##*.}" != 0 ] || [ "${subnet%%.*}" = 0 ] ||
		[ "${subnet%%.*}" = 127 ] || [ "${subnet%%.*}" -ge 224 ]; then
		tinyfecvpn_error "$config" INVALID_SUBNET
		return 1
	fi
	if ! tinyfecvpn_fec "$fec" || ! tinyfecvpn_uint "$fec_mode" 0 1 ||
		! tinyfecvpn_uint "$fec_timeout" 0 1000 || ! tinyfecvpn_uint "$fec_mtu" 100 1500 ||
		! tinyfecvpn_uint "$tun_mtu" 576 1500 || ! tinyfecvpn_uint "$mssfix" 0 1500 ||
		! tinyfecvpn_uint "$report" 0 3600 || ! tinyfecvpn_uint "$log_level" 0 6 ||
		! tinyfecvpn_uint "$startup_timeout" 1 60; then
		tinyfecvpn_error "$config" INVALID_CONFIG
		return 1
	fi
	# In FEC mode 1 the program does not split packets.
	if [ "$fec_mode" = 1 ] && [ "$tun_mtu" -gt "$fec_mtu" ]; then
		tinyfecvpn_error "$config" INVALID_MTU
		return 1
	fi
	if [ "$mssfix" != 0 ] && [ "$mssfix" -lt 100 ]; then
		tinyfecvpn_error "$config" INVALID_MSS
		return 1
	fi
	case "${keep_reconnect:-1}" in 0|1) ;; *) tinyfecvpn_error "$config" INVALID_CONFIG; return 1 ;; esac
	case "${defaultroute:-0}" in 0|1) ;; *) tinyfecvpn_error "$config" INVALID_CONFIG; return 1 ;; esac
	# Upstream uses sscanf("%s") for keys; reject whitespace/truncation and
	# bound the input before it reaches the fixed-size upstream buffer.
	case "$key" in *[[:space:]]*) tinyfecvpn_error "$config" INVALID_KEY; return 1 ;; esac
	[ ${#key} -le 128 ] || { tinyfecvpn_error "$config" INVALID_KEY; return 1; }
	case "$role" in
		client)
			if ! tinyfecvpn_host "$server" || ! tinyfecvpn_uint "$server_port" 1 65535; then
				tinyfecvpn_error "$config" INVALID_SERVER; return 1
			fi
			local_ip="${subnet%.*}.2"
			peer_ip="${subnet%.*}.1"
			;;
		server)
			if ! tinyfecvpn_ipv4 "$listen_addr" || ! tinyfecvpn_uint "$listen_port" 1 65535; then
				tinyfecvpn_error "$config" INVALID_LISTEN; return 1
			fi
			local_ip="${subnet%.*}.1"
			peer_ip="${subnet%.*}.2"
			;;
		*) tinyfecvpn_error "$config" INVALID_ROLE; return 1 ;;
	esac
	if [ -n "$gateway" ] && { [ "$role" != client ] || [ "$gateway" != "$peer_ip" ]; }; then
		tinyfecvpn_error "$config" INVALID_GATEWAY
		return 1
	fi
	config_load network
	config_foreach tinyfecvpn_check_instance interface
	[ -z "$conflict" ] || { tinyfecvpn_error "$config" "$conflict"; return 1; }
	if ip link show dev "$tunname" >/dev/null 2>&1; then
		tinyfecvpn_error "$config" DEVICE_IN_USE
		return 1
	fi
	[ -z "$tunlink" ] || proto_add_host_dependency "$config" '' "$tunlink"
	if [ "$role" = client ]; then
		addresses="$(resolveip -4 -t 5 "$server" 2>/dev/null)"
		address=
		for address in $addresses; do tinyfecvpn_ipv4 "$address" && break; address=; done
		if [ -z "$address" ]; then
			# DNS/network failures are transient; allow a later retry.
			proto_notify_error "$config" RESOLVE_FAILED
			proto_setup_failed "$config"
			return 1
		fi
		proto_add_host_dependency "$config" "$address" "$tunlink"
	fi

	# These upstream switches take NO arguments, despite the help text.
	# Omit --persist-tun so closing the process deletes the device.
	set -- /usr/bin/tinyvpn --tun-dev "$tunname" --manual-set-tun \
		--sub-net "$subnet" -f "$fec" --mode "$fec_mode" --timeout "$fec_timeout" \
		--mtu "$fec_mtu" --tun-mtu "$tun_mtu" --mssfix "$mssfix" \
		--log-level "$log_level" --disable-color
	[ "$report" = 0 ] || set -- "$@" --report "$report"
	if [ "$role" = client ]; then
		set -- "$@" -c -r "$address:$server_port"
		[ "${keep_reconnect:-1}" = 0 ] || set -- "$@" --keep-reconnect
	else
		set -- "$@" -s -l "$listen_addr:$listen_port"
	fi
	# Attach the argument: upstream scans every argv for --unit-test and
	# logging switches before getopt, even when an argv is a key value.
	# Upstream accepts the key through argv only. Never eval user input.
	[ -z "$key" ] || set -- "$@" "-k$key"
	proto_run_command "$config" "$@" || { proto_setup_failed "$config"; return 1; }
	elapsed=0
	while ! ip link show dev "$tunname" >/dev/null 2>&1; do
		if [ "$elapsed" -ge "$startup_timeout" ]; then
			proto_notify_error "$config" TUN_TIMEOUT
			proto_kill_command "$config"
			proto_setup_failed "$config"
			return 1
		fi
		sleep 1
		elapsed=$((elapsed + 1))
	done
	# --manual-set-tun also skips SIOCSIFMTU. Set MTU before publishing the
	# device; netifd owns addresses and brings the link up (not external=1).
	if ! ip link set dev "$tunname" mtu "$tun_mtu"; then
		proto_notify_error "$config" TUN_CONFIG_FAILED
		proto_kill_command "$config"
		proto_setup_failed "$config"
		return 1
	fi
	proto_init_update "$tunname" 1
	proto_add_ipv4_address "$local_ip" 32 '' "$peer_ip"
	if [ -n "$gateway" ] && [ "$defaultroute" != 0 ]; then
		proto_add_ipv4_route 0.0.0.0 0 "$peer_ip"
	fi
	for address in $dns; do proto_add_dns_server "$address"; done
	proto_send_update "$config" || {
		proto_kill_command "$config"
		proto_setup_failed "$config"
		return 1
	}
}

proto_tinyfecvpn_teardown() {
	proto_kill_command "$1"
}

[ -n "$INCLUDE_ONLY" ] || add_protocol tinyfecvpn
