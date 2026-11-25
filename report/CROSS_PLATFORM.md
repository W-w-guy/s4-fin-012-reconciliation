# 跨平台实跑记录（同一份 zip，Windows 与 Ubuntu 各跑一遍）

## 传输方式

交付包先在 Windows 侧打成 `recon2873.zip`，经本机 HTTP 服务（192.168.112.1:8899）
下载到 Ubuntu 虚拟机（192.168.112.128）。两侧 zip 的 sha256 完全一致：

```
af30f7cd66dc739e5f44d1b88d9250c86d3f9df832e9bb67a31182261a8c5751  recon2873.zip
```

## 结果对照

| 检查项 | Windows 11 / Python 3.11.7 | Ubuntu 24.04.5 / Python 3.12.3 |
| --- | --- | --- |
| SHA256SUMS 逐条复核 | 37/37 OK，0 失败 | 37/37 OK，0 失败 |
| `tools/gen.py` | rc=0，examples=20 categories=7 | rc=0，examples=20 categories=7 |
| `tools/verify.py` | **38/38 PASS**，rc=0 | **38/38 PASS**，rc=0 |
| `pytest tests -q` | 21 passed | 21 passed |
| `-m pkg.server` 起服务后 health | 200 | 200 |
| 超限核销（AC3）真实 HTTP | **422** | **422** |
| 合法核销 | **201** | **201** |
| 不存在的分期 | **404** | **404** |
| `python pkg/server.py` 第二入口 | health 200 | health 200 |
| 重跑 gen 后校验和 | 37/37 OK | 37/37 OK |
| PII 扫描 | scanned=35 hits=0 | scanned=35 hits=0 |
| 包内可校验文件数 | 38 | 38 |

> AC3 的原文是「返回 422」。两侧都**真的起了 HTTP 服务、真的发了 POST**，
> 拿到的是 HTTP 状态码，不是内部函数返回值。Windows 侧与 Ubuntu 侧的 detail 文本逐字相同：
> `{"detail": "核销合计 205000 分超过流水金额 120000 分"}`

## 跨平台一致性怎么证明的

`SHA256SUMS.txt` 由 **Windows 侧生成**，Ubuntu 侧执行 `sha256sum -c` 得到 **37/37 OK**。
这说明包内 37 个受校验文件（含 `docs/` 下全部 CSV 与 Markdown 产物、两份迁移 SQL）在两个平台上
**逐字节相同**，不是"看起来一样"。Ubuntu 侧重跑 `tools/gen.py` 后再校验仍是 37/37 OK，说明生成幂等。

## Ubuntu 侧发现并修掉的真问题

第一次在 Ubuntu 上跑 `python -m pkg.server --port 8011` 直接崩：

```
ModuleNotFoundError: No module named 'engine'
```

原因：`pkg/server.py` 里写的是 `import engine`（同目录平铺模块），
`python -m pkg.server` 时 `sys.path` 里是包根而不是 `pkg/`，于是找不到。
Windows 侧没暴露这条——我之前的本机验证是 `sys.path.insert` 过 `pkg/` 后 import 的，
等于绕开了真实启动路径。

修法：在 `pkg/server.py`、`receipt.py`、`scenarios.py` 顶部统一加

```python
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
```

修完后两种入口（`-m pkg.server` 与 `python pkg/server.py`）在两个平台都能起服务。
这条正好印证 playbook 里那句「Windows 全绿不等于可用」。

## 两个平台的原始日志

- `report/windows_run.log` —— Windows 侧完整输出
- `report/ubuntu_run.log` —— Ubuntu 侧完整输出（原样传回，sha256 `990e2868c99d54a7c88d8ddcbee02a1a03017b2db03c02346d4ad943b2ed4a13`，与虚拟机内文件逐字节一致）

> `report/` 是打包时的快照，按约定不参与 `SHA256SUMS.txt` 校验（买家自己重跑会覆盖这里）。
