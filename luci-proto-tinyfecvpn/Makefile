# SPDX-License-Identifier: GPL-3.0-only
include $(TOPDIR)/rules.mk

LUCI_TITLE:=Support for tinyfecVPN interfaces
LUCI_DEPENDS:=+luci-base +tinyfecvpn +netifd +resolveip +ip

PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-3.0-only

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
