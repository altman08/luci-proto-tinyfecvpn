# luci-proto-tinyfecvpn

在 OpenWrt / ImmortalWrt 的 **网络 → 接口** 中管理 tinyfecVPN，支持客户端、服务端、多实例和简体中文。配置保存在 `/etc/config/network`，协议名为 `tinyfecvpn`。

本仓库仅提供 LuCI JavaScript 协议插件和 netifd 协议脚本，依赖软件源已有的 `tinyfecvpn`（`/usr/bin/tinyvpn`），不包含后端源码、后端软件包或独立 init 服务。依赖包会进一步安装 `kmod-tun`、`libstdcpp` 等运行依赖。

## 构建

在准备好的 OpenWrt / ImmortalWrt 源码或 SDK 中添加 feed：

```sh
echo 'src-git tinyfecproto https://github.com/altman08/luci-proto-tinyfecvpn.git' >> feeds.conf.default
./scripts/feeds update tinyfecproto
./scripts/feeds install -p tinyfecproto luci-proto-tinyfecvpn
```

如果使用 `feeds.conf`，修改该文件。已有同名 feed 时替换原条目，避免重复添加。提前更新、安装标准 `packages`、`luci` feed，并确认它们提供 `tinyfecvpn`、`netifd`、`resolveip` 和 `ip`。

在 `make menuconfig` 中选择 `LuCI → Protocols → luci-proto-tinyfecvpn`，以及简体中文翻译；或者在 `.config` 中设置以下配置项（已有项应修改，不能重复追加）：

```text
CONFIG_PACKAGE_luci-proto-tinyfecvpn=m
CONFIG_LUCI_LANG_zh_Hans=y
CONFIG_PACKAGE_luci-i18n-tinyfecvpn-zh-cn=m
```

```sh
make defconfig
make package/luci-proto-tinyfecvpn/compile V=s
```

中文包是同一个 LuCI 源码包生成的子包，不需要单独的编译目标。插件和中文包均为 `all` 架构；依赖包仍须匹配路由器的系统版本、架构，`kmod-tun` 还须匹配内核。

## GitHub Actions 打包与发布

[打包工作流](.github/workflows/build-openwrt.yml) 仅通过 Actions 页面中的 **Run workflow** 手动触发，运行检查并打包：

| SDK | 格式 | 发布内容 |
| --- | --- | --- |
| OpenWrt 24.10.5 | IPK | `luci-proto-tinyfecvpn`、`luci-i18n-tinyfecvpn-zh-cn`、SHA256 校验文件 |
| OpenWrt 25.12.5 | APK | 同上 |

仅用 x86_64 SDK 构建通用 LuCI 包，无需按 CPU 架构重复构建。后端来自 SDK 的标准 packages feed，构建过程中可能编译依赖，但不会将后端或其他依赖收集到发布产物。SDK 版本放在工作流 matrix 中，可按需要调整。

- `tag` 留空：只打包，产物保存在 Actions artifacts。
- 填写 `tag`：全部构建成功后，为所选提交创建或更新 GitHub Release，可通过 `prerelease` 标记预发布。含连字符的标签也标记为预发布。

Release 产物名称带 SDK 后缀以避免重名，安装时选择匹配系统包格式和版本的一组插件/翻译包。源码 `PKG_VERSION` 在 `luci-proto-tinyfecvpn/Makefile` 中维护，发布标签不会自动修改包版本。安装时由包管理器从系统软件源获取 `tinyfecvpn` 依赖。

## 使用

安装后进入 **网络 → 接口 → 添加新接口**，协议选择 **tinyfecVPN**，不需要选择已有物理网卡。安装中文包并选用中文界面后刷新浏览器。

客户端示例：

```uci
config interface 'fec0'
        option proto 'tinyfecvpn'
        option role 'client'
        option server 'vpn.example.com'
        option server_port '4096'
        option subnet '10.22.22.0'
        option key 'replace-with-your-key'
        option fec '20:10'
        option tunlink 'wan'
        option keep_reconnect '1'
```

服务端示例（另一台设备）：

```uci
config interface 'fec0'
        option proto 'tinyfecvpn'
        option role 'server'
        option listen_addr '0.0.0.0'
        option listen_port '4096'
        option subnet '10.22.22.0'
        option key 'replace-with-your-key'
        option fec '20:10'
```

双方网段和密钥保持一致。服务端 VPN 地址为 `10.22.22.1`，客户端为 `10.22.22.2`，使用 `/32 + peer` 的三层点对点配置，不会自动创建整个 `/24` 的直连路由。默认设备名称为 `tfec-<接口名>`，Linux 设备名最多 15 个字符，接口名较长时需指定较短的 `tunname`。

在接口的防火墙设置中选择合适区域；服务器公网 UDP 监听端口、访问远端 LAN 所需静态路由和两端转发规则也需要单独配置。本插件不会直接插入防火墙规则。它使用 TUN，不提供二层桥接或 overlay DHCP。

默认不发布 VPN 默认路由。客户端确实需要全局出口时，将“IPv4 网关”填为服务端 VPN 地址（例如 `10.22.22.1`），并保持“使用默认网关”启用；服务端另行配置转发、出口路由或 NAT。插件对实际解析选中的服务器 IPv4 地址建立底层路由依赖，避免建立流量进入隧道自身。自定义 DNS 可使用接口页的公共 DNS 设置。

## 参数与生命周期

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `fec` | `20:10` | 每 20 个数据包发送 10 个冗余包；也支持 `1:3,2:4,10:6,20:10` |
| `fec_mode` | `0` | `0` 支持分包；`1` 偏重低延迟，不分包 |
| `fec_timeout` | `8` | FEC 排队超时，毫秒 |
| `fec_mtu` | `1250` | FEC 载荷大小；不含外层开销，不能与 TUN MTU 混淆 |
| `tun_mtu` | `1500` | TUN MTU；模式 1 要求不大于 FEC MTU，例如 `1200` |
| `mssfix` | `1250` | 上游据此推导 TCP MSS 上限；`0` 禁用 |
| `keep_reconnect` | `1` | 仅客户端，断线或被新客户端替代后继续尝试重连 |
| `report` | `0` | 流量报告间隔，秒；`0` 时不传 `--report` |
| `log_level` | `3` | 默认警告；`4/5/6` 会记录包含密钥的命令行 |
| `startup_timeout` | `10` | 本地 TUN 创建超时，秒 |
| `tunlink` | 空 | 可选的底层网络依赖；不等于强制绑定物理网卡 |

tinyvpn 以前台进程运行，由 netifd 管理。插件使用不带参数的 `--manual-set-tun`，由 netifd 配置地址和启用接口；不启用 `--persist-tun`，进程退出后自动删除 TUN。配置错误会阻止重复启动，DNS 和启动过程失败允许后续重试。

每个服务端实例同时只支持一个客户端。多个实例应使用不同 TUN 名称、VPN 网段和不冲突的监听地址/端口；插件会检查这些冲突，即使另一个实例尚未启动。不要同时通过手动命令或其他服务管理同一个 TUN。

`-k` 只提供简单 XOR 混淆，不提供认证加密。插件默认警告日志，避免上游在信息级启动日志中输出密钥，但密钥仍以明文保存在 UCI，并出现在进程参数中。密钥可留空，非空时限制为不含空白的最多 128 字节。

## 验证与排查

```sh
ifup fec0
ifstatus fec0
ip address show dev tfec-fec0
ping -c 3 10.22.22.1
logread | grep -E 'netifd|tinyvpn'
```

接口“已启动”仅表示本地 TUN 就绪，不代表对端在线。无法通信时检查公网 UDP 可达性、密钥、两端网段、路由和防火墙。当前没有独立的 peer 在线状态或 FEC 统计面板。

协议列表未出现时，先检查 `/lib/netifd/proto/tinyfecvpn.sh`、`/www/luci-static/resources/protocol/tinyfecvpn.js` 是否存在；netifd 首次加载新协议需要重启网络服务，可能中断远程连接，之后刷新 LuCI。不要仅为前端文件更新重启网络。

| 错误码 | 含义 |
| --- | --- |
| `MISSING_BINARY` / `MISSING_TUN` | 后端程序或 TUN 内核支持缺失 |
| `DEVICE_IN_USE` / `SUBNET_IN_USE` / `PORT_IN_USE` | 设备、网段或监听端口冲突 |
| `RESOLVE_FAILED` | 远端服务器 IPv4 解析失败 |
| `TUN_TIMEOUT` / `TUN_CONFIG_FAILED` | 本地 TUN 未及时创建，或无法设置 MTU |
| `INVALID_MTU` | 模式 1 的 TUN MTU 大于 FEC MTU |
| `INVALID_*` | 配置无效，检查对应字段 |

独立源码目录可执行以下检查，不需要路由器或 OpenWrt 构建环境：

```sh
sh -n luci-proto-tinyfecvpn/root/lib/netifd/proto/tinyfecvpn.sh
node --check luci-proto-tinyfecvpn/htdocs/luci-static/resources/protocol/tinyfecvpn.js
shellcheck -s ash luci-proto-tinyfecvpn/root/lib/netifd/proto/tinyfecvpn.sh
shellcheck scripts/collect-packages.sh
python3 -m unittest discover -s tests -v
```

测试需要 Python 3、Node.js、Bash；通过模拟 netifd/IP 命令验证生命周期、地址角色、路由依赖、参数边界、失败清理和发布包过滤，不改变宿主网络。它不能替代实际设备上客户端与服务端的连通性测试。

## 许可证

本插件使用 GPL-3.0-only，见 [LICENSE](LICENSE)。tinyfecVPN 后端由其上游单独授权。
