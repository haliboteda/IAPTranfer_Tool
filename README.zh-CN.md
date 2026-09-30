# IAPTranfer_Tool
用于向 OpenPLC 传输 bin 文件

English: [README.md](README.md)

## 从源码编译

这个仓库出两个程序：`IAPTool`（给固件签名并上传）和 `PortTool`（端口测试面板）。
两个都只需要 Go 1.23 或更新的版本。第一次编译要下载三个模块，所以要能上网。

在仓库根目录运行：

```
go build -o IAPTool .                     # 本机用的 IAPTool
go build -o PortTool ./cmd/porttool       # 本机用的 PortTool
```

在 Windows 上把输出文件命名为 `IAPTool.exe` 和 `PortTool.exe`。

要给别的系统编译，设置 `GOOS` / `GOARCH`（不需要 C 编译器）：

```
GOOS=linux   GOARCH=amd64 CGO_ENABLED=0 go build -o PortTool ./cmd/porttool
GOOS=darwin  GOARCH=arm64 go build -o PortTool ./cmd/porttool      # Apple 芯片
GOOS=windows GOARCH=amd64 go build -o PortTool.exe ./cmd/porttool
```

PortTool 的网页已经编进可执行文件，测试方案没有：把 `TestCase/plans/*.json`
拷到 `PortTool` 旁边的 `plans/` 文件夹里。

在 Linux 上，运行 PortTool 的用户要有串口的访问权限，通常是加入 `dialout` 组。

`compile_tool.sh` 和 `build.py` 是维护者出版本用的脚本：它们还会编固件、
往 Arduino 板卡包里填东西，需要其他 OpenPLC 仓库。只编这两个工具用不到它们。

## 用法

```
IAPTool cdc    <file.bin> <port>       [--key=<key.pem>] [--cert=<cert.txt>]
IAPTool ether  <file.bin> <ip>         [--key=<key.pem>] [--cert=<cert.txt>]
IAPTool flashboot <boot.bin> <ip>      --key=<owner.pem>
IAPTool sign   <file.bin> [<key.pem>]  [--key=<key.pem>] [--out=<prefix>]
IAPTool genkey [<name>]
IAPTool pubkey [<key.pem>]
IAPTool cert   [<leafPubHex>]          [--key=<root.pem>]

IAPTool getowner <ip>
IAPTool takeown  <ip> --key=<owner.pem>
IAPTool setowner <ip> --current-key=<owner.pem> --new-key=<next.pem>
```

## 固件签名

bootloader 只运行 ECDSA P-256 签名能用它内置的 `fw_public_key[64]`
（`IAPServer/fw_pubkey.c`）验过的镜像，所以每个镜像烧录前都要先签名。
签名功能就在这个工具里 —— 不用 `openssl`，不用 shell 脚本，也不用 `chmod +x`。

```sh
# 不往磁盘写任何东西：镜像在内存里签好，直接发给设备。
IAPTool cdc app.bin COM5 --key=fw_signing_key.pem
```

`sign` 会写出 `<name>.sha256`、`<name>.size` 和 `<name>.sig`（原始 64 字节
r||s），给需要签名文件的人用；上传不用这些文件：上传时还要对板子发来的新挑战
签名，这需要私钥本身。

工具不记录也不比较固件版本。只要镜像能用板子信任的密钥验过就会烧进去，
不管里面是什么 —— 应用程序的版本由作者自己管。

### 密钥从哪里来

按以下顺序查找：

1. 命令行上的 `--key=<path>`
2. `local_config.json` 里的 `"signing_key"`（写绝对路径）
3. `<用户配置目录>/openplc/keys/fw_signing_key.pem`（`os.UserConfigDir()`；
   Windows 上是 `%AppData%`）—— 工具包升级后仍然保留
4. 可执行文件旁边的 `keys/fw_signing_key.pem`
5. 仅限上传：可执行文件旁边的 `keys/published_root.TEST_ONLY.pem`，
   即 `compile_tool.sh` 附带的公开根密钥。只有未认领的板子接受它；每次使用都会打印警告

Arduino IDE 不传密钥，所以它靠的是第 3–5 步。
`local_config.json` 和 `keys/` 相对于**可执行文件**查找，不是当前目录。

## 没有根密钥时上传

一个人一把密钥时下面这些都不需要：那把密钥就是根，它给自己签证书，一切照常工作。

由一位管理员持有根的团队才需要。每位同事保留自己的密钥；管理员签发一张证书，
说明那把密钥被授权，根私钥始终不离开管理员的电脑。

```sh
# 在同事的电脑上
IAPTool genkey fw_signing_key               # 同事自己的密钥；放到上面第 3 步的位置
IAPTool pubkey                              # 128 个十六进制字符 —— 把这些发出去

# 在管理员的电脑上
IAPTool cert <那 128 个十六进制字符> --key=root.pem > colleague.cert

# 回到同事的电脑：放在它所对应的密钥旁边
#   <用户配置目录>/openplc/keys/fw_signing_key.pem.cert
IAPTool ether app.bin 192.168.1.50           # 别的都不用改
```

证书放在 `<它对应的密钥>.cert`，所以密钥和证书不会配错；Arduino IDE 不用任何配置
就能找到它，道理和找到密钥一样。`--cert=<file>` 可以覆盖。

撤销某位同事用 `IAPTool revoke --leaf=<他的公钥>`：这个叶证书从下一次上传起被拒绝，
它已经装上的固件继续运行（`IAPTool getapprevoked` 能找出这些板子）。把板子交给新的根
（`IAPTool setowner`）会让旧根签发的所有证书失效，包括已经装上的固件。见
`OpenPLC_Docs/docs/modules/M2-ownership.md`。

### 不匹配在传输前就发现

发送任何东西之前，工具先问 bootloader（`getpubkey`）它按哪个根验签，并检查要出示的
证书是不是那个根签发的。对自签证书来说，这和「我的密钥是不是板子的密钥」是同一个
问题，所以只有一个检查，不分两条路。

不匹配时立即停止上传并打印两边的指纹，而不是把整个镜像传完、最后被板子拒绝。
太旧、不认识 `getpubkey` 的 bootloader 会回 `Unknown command`；这时跳过检查并打印
警告，上传照旧进行。

### 生成密钥

要轮换签名密钥，运行 `IAPServer/keys/rotate_keys.sh` —— 它调用下面的命令，
分发每一份拷贝，并先做备份。这一步也可以单独用：

```sh
IAPTool genkey my_release_key > fw_pubkey.inc
```

`genkey` 写出 `my_release_key.pem`（私钥，权限 0600），并在标准输出打印
`IAPServer/keys/fw_pubkey.inc` 的内容。这个文件被固件 `#include`，所以新密钥生效前
要重新编译 bootloader 并用 ST-Link 重新烧录。私钥离线保存，它永远不上设备。

密钥和 `openssl` 双向通用 —— `genkey` 输出标准 SEC1 PEM，`sign` 接受 SEC1 或 PKCS#8。

## 板子归属

板子出厂时信任本项目公开的签名密钥，也就是说任何人都能签出它会运行的固件。
认领后它绑定到你自己的密钥，从此别的都起不来。

```sh
IAPTool genkey owner                 # 写出 owner.pem —— 离线保存
IAPTool getowner 192.168.0.30        # 这块板子信任哪把密钥？
IAPTool takeown  192.168.0.30 --key=owner.pem
IAPTool setowner 192.168.0.30 --current-key=owner.pem --new-key=next.pem
```

## 更换 bootloader

`flashboot` 不用 ST-Link 就把新的 bootloader 写进扇区 0，并把归属记录带过去，
板子仍然保持已认领。

```sh
IAPTool flashboot boot.bin 192.168.0.30 --key=owner.pem
```

密钥必须是归属根本身：板子按根检查 bootloader 镜像，不按叶证书。
未认领的板子没有根可查，所以改为要求在当前这次启动时按住 BOOT0。

**过程中不要断电。** 板子正在被改写的扇区里运行；中断后它无法启动，
只有 ST-Link 能救回来。

除非板子这次启动时按住了 BOOT0，否则 `takeown` 会被拒绝。
第一次认领不带签名 —— 还没有归属者能签 —— 所以唯一能有的关卡就是人在现场。
它也不会退回去用 `local_config.json` 里的签名密钥：用错密钥认领了板子，
只能用 ST-Link 重烧 bootloader 才能撤销，因为归属记录就在 bootloader 自己的 flash 扇区里。

`setowner` 不用按键。它由板子当前信任的密钥签名，所以可以通过网络交接；
偷来的记录也接管不了板子 —— 板子检查的是签名，不是代数。

## 回收撤销槽位

归属区能放 96 条撤销记录，而且只追加，所以要拿回槽位只能擦掉它所在的 flash 扇区。
只剩 8 个时启动日志开始提示。

```sh
IAPTool setowner 192.168.0.30 --current-key=owner.pem --new-key=next.pem --wipe
```

`--wipe` 在交接板子的**同时**让归属区只剩新的那条记录。板子为此擦除并重写
自己的扇区，所以会复位，而且**擦除时断电就要按住 BOOT0 复位、再通过 USB DFU 重烧**
—— 和 `flashboot` 需要的恢复方法一样。普通的 `setowner` 只追加一条记录，没有这个风险；
板子不会自己决定擦除。

不带 `--wipe` 换根会让旧根签发的所有叶证书失效，不用再一个个撤销 ——
但已经被那些撤销记录占掉的槽位不会释放。
