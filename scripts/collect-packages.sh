#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

: "${ARCH:?Set ARCH to the SDK package architecture}"
: "${SDK:?Set SDK to the OpenWrt version}"
package_dir="${1:-bin/packages/${ARCH}/action}"
output_dir="${2:-release}"
shopt -s nullglob
frontend=("${package_dir}"/luci-proto-tinyfecvpn_*.ipk "${package_dir}"/luci-proto-tinyfecvpn-[0-9]*.apk)
translations=("${package_dir}"/luci-i18n-tinyfecvpn-zh-cn_*.ipk "${package_dir}"/luci-i18n-tinyfecvpn-zh-cn-[0-9]*.apk)
if ((${#frontend[@]} == 0 || ${#translations[@]} == 0)); then
	echo "::error::Missing luci-proto-tinyfecvpn or luci-i18n-tinyfecvpn-zh-cn in ${package_dir}" >&2
	exit 1
fi
mkdir -p "$output_dir"
for package in "${frontend[@]}" "${translations[@]}"; do
	filename="${package##*/}"
	# LuCI packages are architecture-independent; distinguish SDK builds only.
	cp "$package" "${output_dir}/${filename%.*}_openwrt-${SDK}.${filename##*.}"
done
(
	cd "$output_dir"
	packages=(*.ipk *.apk)
	sha256sum "${packages[@]}" > "sha256sums_openwrt-${SDK}"
)
