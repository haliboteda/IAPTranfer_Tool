# 端口工装协议契约测试

用例 **H4**，两半，一条命令一起跑：

1. **固件侧** —— 把端口工装的**真实固件源码**（`open_plc_cube_ide/TestCase/porttool/` 的
   `porttool.c` `porttool_cmd.c` `porttool_din.c` `porttool_relay.c` `porttool_handover.c`）
   在 PC 上原生编译并运行，敲一串 `pt.*` 命令，检查它吐出来的每一行，写出 `caps_golden.txt`
2. **上位机侧** —— Go 测试拿那份字节验三层，**带 `-race`**：
   `internal/ptproto`（解析）、`internal/ptboard`（连接层）、`internal/ptpanel`（面板的 HTTP 面）

**两半不能分开跑。**分开的话它们会各自漂，而且都还是绿的 —— 那正是这条用例要防的事。

```
porttool_caps/
├── build.py          ← 入口，两半都由它跑
├── caps_golden.txt   ← 产物，也是 Go 测试的夹具
├── ptproto_test.go   ← 解析层
├── ptboard_test.go   ← 连接层，假串口回放
├── ptpanel_test.go   ← 面板 HTTP 面，httptest 端到端
└── harness/          ← C 那半。⚠️ 放子目录是因为 CGO_ENABLED=1 时
    ├── test_main.c      Go 会去编译包目录里的 .c 文件并直接拒绝整个包
    └── stubs/
```

## 为什么要有这一条

`pt.caps` 是**上位机面板的唯一数据来源** —— 端口树、子通道复选框、参数控件全按它渲染。
所以它一旦出问题，问题的形状是「面板静默地画错」，不是「报错」：

- 某行超过 192 字节的单行上限 → 被固件截断，面板少一个端口或少一个参数
- 端口声明了 `params=duty` 却不回报 `duty=` 的当前值 → 面板画出一个不知道该显示什么状态的控件
- 表头的 `lines=` 和实际行数对不上 → 上位机读少了或读多了，卡在下一条命令上
- 交权目标分组漏掉一个 → 那个测试在面板上根本不存在

这些**全都能在 PC 上判**，所以就在 PC 上判，不要等板子上了工作台才发现。

## 跑

```
python build.py
```

编译器解析顺序和隔壁 H2 一样：`$CC` → `config/machine.py` 的 `HOST_CC` → PATH 上的
`gcc` / `clang`。退出码 `0` = 全过，`1` = 有检查没过，`2` = 编译或运行失败。

找得到 C 编译器时 Go 那半会带 `-race` 跑（竞态检测要 cgo）。**这不是装饰** ——
`ptboard` 的读串口、发命令、浏览器订阅分别在三个 goroutine 上，
**第一次开 `-race` 就抓到一个真的**：订阅通道在读线程还在向它投递时被关掉了，
放到生产里就是「有人关浏览器标签的那一刻正好来了一帧」→ panic。

## 只假造了一层

**唯一被 stub 的头文件是 `stm32h7xx_hal.h`**（`stubs/`）。它上面那些全是固件自己的：
真的 `Core/Inc/main.h`、真的 `Core/Inc/usart.h`、真的 `Core/Inc/relay.h`、
真的 `TestCase/common/port_din.h`。

**这是刻意的** —— 测试断言里的通道数（`RELAY_COUNT` = 6、`PORT_DIN_COUNT` = 8）
因此是固件自己的常量，不是一份会漂的抄写。

`stubs/hal_stub.c` 是假硬件：冻住的时钟、永远收不到字节的串口、以及会记住自己被驱动成
什么样的继电器 —— 最后这个是用来验 `pt.stop` 真的把继电器全放开了，因为**板子上没有触点
回读通道，这是唯一能检查它的地方**。

`stubs/entries_stub.c` 是 14 个交权入口的地址。它们**被调用就让整个测试失败退出**：
交权是单向门，真跑起来会把 harness 一起带走，所以「不小心交权了」必须是红的，不能是静默通过。

`test_main.c` 用 `#include "porttool.c"` 而不是链接它，因为 `dispatch()` 和 `reply_caps()`
是 `static`。这样做是为了**不给固件加一个只有测试用得上的入口** —— 生产代码的接口不该为测试变宽。

## 检查了什么

**行规矩**

- 每一行都在 `PORTTOOL_LINE_MAX` 以内（上限从 `porttool.h` 读，不写死）
- 每一行以 `OK ` / `ERR ` / `!` 开头 —— 上位机靠首字符分流，不需要状态机

**`pt.caps` 表头**

- `porttool=` 与 `porttool.c` 的 `PORTTOOL_VERSION` 一致
- `lines=` 等于后面实际跟着的行数
- `ports=` 等于 `port=` 行的条数

**`pt.caps` 每一行**

- 同一行内没有重复的 key（所以用有序列表解析，不是 dict —— dict 会把这个 bug 藏掉）
- 每行都带 `port` `kind` `blk` `term` `channels` `loop`
- **`kind=` 只能是 `session` / `handover` / `run`** —— 不认识的 kind 是面板画不出来的行
- `loop=` 只能是 `ctrl` / `link` / `self` / `none`
- `channels=` 是正整数
- **每个会话声明的 `params=` 里的每一项，都能在同一行里找到对应的当前值**
- 每个交权端口都列了非空的 `targets=`
- **没有两行共用同一个 `port=`** —— 一块硬件一行（DECISIONS.md 17）

**交权分组**

- `pt.handover` 列出的 14 个目标，和 caps 里各端口行的 `targets=` 并集完全相等 ——
  不多不少，且每个只属于一组。锚点可以是会话行也可以是 `kind=run` 行

**一次性动作**

- 每个 `kind=run` 行都列了非空的 `runs=`
- **`pt.run` 无参列出的目标，和 caps 里 `runs=` 的并集完全相等** —— 这一条是
  「方案文件能离线校验」的根据：只能靠敲命令才发现的目标，方案里写错了没人拦
- **没有一个名字同时是 `runs=` 里的和 `targets=` 里的** —— 那会让一个名字对应
  「跑完回来」和「一去不回」两种行为

**会话生命周期**

- `din` 起始 `running=0` → `pt.start` 后 `running=1` → `pt.stop all` 后回到 `0`
- caps 回报的 `ch=` 就是 `pt.start` 给的那一组
- `pt.stop all` 之后六路继电器全部释放

**拒绝**

五种都要回 `ERR` 且带得出原因：通道号越界、数值参数不是数字、端口名不存在、
对交权端口发会话命令、未知命令。并且**被拒绝的参数没有渗进会话** ——
这条对应工装的铁律「参数给了但写错，拒绝启动，不静默沿用旧值」。

## 上位机侧还检查了什么

`ptboard_test.go` 用一个**假串口**回放同一份脚本，覆盖只有真链路上才会出问题的四件事：

- **命令在飞的时候会话还在推帧** —— 一边收 `pt.caps` 的 11 行回复，一边灌 20 帧 `!din`，
  回复必须完整、帧必须照样送到订阅者。这是常态不是边角：会话不会因为有人敲了命令就暂停
- **拒绝保留板子的原话** —— `ERR` 变成 `RefusedError`，`Reason` 是板子那句「must be channels 1..8」，
  不是「启动失败」
- **长度没人声明的多行回复** —— `pt.handover` 一个目标一行、从不说有几行，靠静默间隔收尾
- **backlog 收全四类行**，且 `Seq` 无缺口 —— 落后的视图靠序号缺口知道自己漏了什么

## 面板那层还检查了什么

`ptpanel_test.go` 用 `httptest` 把整条链接到同一个假板子上：**连接 → caps → 命令 → SSE**。
面板整个页面都是按 `/api/state` 渲染的，所以这层出错的形状是「画错」，不是「报错」：

- **端口清单是板子报的，不是面板自己的** —— **14 行**（8 个会话 + 6 行交权，`ports=14` 数的就是行数）、
  `din` 的 8 个端子标签是 `D02..D09`（推导来的）、`relay` 的 6 个是 `B01+B02` 这样的配对（固件明说的）、
  `can` **一行带 4 个 target**（`can,can.soak,can.scope,can.echo`）—— 一块硬件在 caps 里只占一行

  ⚠️ **两个不同的 14 不要混**：caps 里 14 **行**端口；交权 **target** 也正好 14 个，但那 14 个归并成 6 行，
  外加 `rs485` 挂在自己的会话行上、`rs232` 刻意不进 caps（`$BOOT/docs/design/DECISIONS.md` 第 17 条）
- **命令之后重读 caps** —— 用脚本里第二次 `pt.caps` 的 `running=1` 卡住这条。
  缓存了的话「停止」按钮会毫无理由地灰着
- **拒绝原文透传** —— `refused` 里是「ch=… must be channels 1..8」，不是「启动失败」
- **SSE 先补 backlog 再推实时帧** —— 晚开的页面必须看到板子之前已经说过的话
- **没连板子就发命令要说人话**

⚠️ **假串口用的是 `io.Pipe`，不是真 COM 口。**这台机器上那对 ELTIMA 虚拟串口
（COM1↔COM2）被占着开不了，所以这层测的是面板逻辑，**不包括串口驱动本身**。
串口驱动那条风险只能在干净机器上插真适配器验（`$BOOT/docs/design/DECISIONS.md` 第 7 条）。

## caps_golden.txt

每次跑都会重写 `caps_golden.txt`，里面是**板子真会写到串口上的字节**（含 `\r\n`）。

它是给上位机 Go 解析器当测试夹具用的。**这个文件永远由这个脚本生成，没有人手敲那些字节** ——
一旦手敲，它就变成了第二份实现，而两份实现只会一起漂。
