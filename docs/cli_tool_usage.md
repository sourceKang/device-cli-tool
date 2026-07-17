# CLI Tool 使用指南

## 目前狀態

`cli_tool` 是可跨專案使用的 CLI 自動化工具骨架。目前 operational scope 是「一次執行一台設備、一個 read-only catalog command」，暫不處理多台設備同時執行。

目前已支援：

- 從 YAML catalog 載入 read-only command。
- 透過 `DeviceDriver` 依 family/model 管理設備差異。
- 透過 `CliTransport` 介面抽象命令執行方式。
- 透過 `SshCliTransport` 包裝本專案既有 SSH session pool。
- SSH connect/auth 前置階段遇到暫時性錯誤時可重試，預設最多 3 次並採指數退避；認證錯誤不重試。
- SSH 與 Serial command 會等待實際 CLI prompt，並以整體 timeout 防止無限等待；不再以固定 sleep 或短暫 idle 判斷 command 已完成。
- 透過 `SerialCliTransport` 使用 serial console，例如 `COM5`。
- 針對 UG 常見 `-- more --` pager prompt 送出 continue，避免長輸出被第一頁截斷。
- 透過 fake transport 進行離線單元測試。
- 手動執行單台設備 read-only smoke 工具，預設跑 `show_version`。
- 透過 `--env-node` 讀取被使用專案的 `node_target.cli`，自動選擇 SSH 或 Serial transport。
- 產生 redacted JSON smoke report。
- CLI output 為空白時一律驗證失敗，即使 catalog 的 `expected_tokens` 為空。
- 提供 fail-closed 的 `show lc st` parser，可產生 JSON-safe line-card Snapshot。
- `--include-output` 會先套用 redaction，且每個 command output 有字數上限。

目前尚未支援：

- 自動執行 CLI config/mutating command。
- 多台設備同時執行或批次併發 smoke。
- IES52XX / OLT140X 真實 command catalog。
- 各系列 prompt、確認訊息、firmware 差異的完整 driver 特化。

## 工具結構

```text
cli_tool/
  catalog/
    data/
      generic.yaml
      neox.yaml
      ies52xx.yaml
      olt140x.yaml
    loader.py
    models.py
  devices/
    base.py
    registry.py
  parsers/
    text_tokens.py
  transport/
    base.py
    serial.py
    ssh_adapter.py
  workflows/
    verify.py
    driver_verify.py

configs/cli_tool/
  generic.yaml
  neox.yaml
  ies52xx.yaml
  olt140x.yaml

tools/
  cli_tool_readonly_smoke.py
```

核心流程：

```text
YAML catalog -> DeviceDriver -> Transport -> Read-only VerifyResult -> JSON report
```

## 新增 Catalog

Catalog 應放在 `configs/cli_tool/`，不得放設備 IP、帳號、密碼、token 或任何 lab-specific secret。

新增命令前需以公開設備文件或已去識別化的實機 output 確認語法、read-only 性質與輸出安全性；候選資訊不等同於已完成實機驗證。

最小格式：

```yaml
family: neox
model: generic
commands:
  show_version:
    mode: exec
    command: "show version"
    readonly: true
    expected_tokens: []
```

多段 read-only command 可用 `commands`：

```yaml
family: neox
model: generic
commands:
  show_interface_detail:
    mode: exec
    readonly: true
    commands:
      - "show interface {interface}"
      - "show running-config interface {interface}"
    expected_tokens:
      - "{interface}"
      - "enable"
```

使用規則：

- `readonly: true` 只允許 show/display 類命令。
- 尚未確認的 IES52XX / OLT140X command 不要先猜進 catalog。
- 若不同 model/firmware 輸出差異很大，應拆 model catalog 或 driver normalizer。
- `expected_tokens` 可先留空，但 output 仍必須包含非空白內容；正式驗證應補上已由 UG 或去敏實機 output 確認的期待值。

## Line-card Preflight 核心能力

本 package 提供 `run_line_card_preflight`，負責執行 catalog 核准的單一 read-only command，並將固定欄寬的 `show lc st` 表格解析成 Snapshot：

```python
from pathlib import Path

from cli_tool.workflows.line_card_preflight import run_line_card_preflight

snapshot = run_line_card_preflight(
    driver,
    transport,
    command_id="show_line_card_status",
    owner="preflight:NODE1",
)
Path("reports/preflight/NODE1.json").write_text(
    snapshot.to_json(),
    encoding="utf-8",
)
```

Snapshot schema：

```json
{
  "schema_version": 1,
  "command": "show lc st",
  "cards": [
    {
      "slot": "1",
      "card_type": "SANITIZED_CARD_TYPE",
      "status": "Active",
      "fw_version": "SANITIZED_FW_VERSION"
    }
  ]
}
```

安全與驗證行為：

- `Card Type`、`Status`、`FW Version` 三個 header 缺任一個即失敗。
- 任一資料列缺少上述欄位值即失敗。
- 空 output 或零資料列不得產生成功 Snapshot。
- parser 遇到未知欄位順序會失敗，不猜測設備資料。
- pytest session cache、per-node 共用、report 路徑與 EMS UI/config 比對由使用專案負責。

目前尚未把 `show lc st` 加入任何內建 family catalog。加入前必須確認適用的設備 family/model、prompt，以及至少一份去敏實機 output；確認後的 catalog command 應使用 `expected_tokens: ["Card Type", "Status", "FW Version"]`。

## 離線使用範例

不連設備時，可用 fake transport 測 catalog 與 workflow：

```python
from cli_tool.catalog.loader import load_driver
from cli_tool.models import CliCommandOutput
from cli_tool.workflows.driver_verify import run_driver_verify


class FakeTransport:
    def run_commands(self, commands, *, owner=None):
        return [CliCommandOutput(command="show version", output="Version: synthetic")]


driver = load_driver("configs/cli_tool/generic.yaml")
result = run_driver_verify(driver, FakeTransport(), "show_version")

assert result.passed
```

## 手動 Read-only Smoke

第一次實機 smoke 僅建議跑 read-only `show_version`，且不要加 `--include-output`。

使用被使用專案 config 的 PowerShell 範例：

```powershell
.\.venv\Scripts\python.exe -m tools.cli_tool_readonly_smoke `
  --env-node node3 `
  --auth-profile default
```

`--env-node` 會先嘗試使用原始 node key；若找不到，會再嘗試大寫 key，所以 `node1` 可 fallback 到 `NODE1`。

被使用專案可在 `configs/test_targets.yaml` 的 node 下新增 `cli` 區段：

```yaml
nodes:
  NODE1:
    device_name: "example-device"
    device_ip: "192.0.2.10"
    chassis: "IES4204"
    cli:
      transport: "serial"
      serial_port: "COM5"
      baudrate: 115200
      timeout: 15
      username: "admin"
      password_env: "CLI_TOOL_SERIAL_PASSWORD"
      catalog: "builtin:ies52xx"
      default_command: "show_version"
      report_dir: "reports/cli-tool"
```

支援的 `node_target.cli` 欄位：

| 欄位 | 用途 | 預設值 |
| --- | --- | --- |
| `transport` | `ssh` 或 `serial` | `ssh` |
| `host` | SSH host；未設定時使用 node 的 `device_ip` | node `device_ip` |
| `serial_port` | Serial console port，例如 `COM5` | 無 |
| `username` | CLI 登入帳號 | auth profile 的 readwrite username |
| `password_env` | 保存 CLI 密碼的環境變數名稱 | auth profile；獨立模式為 `CLI_TOOL_SSH_PASSWORD` |
| `catalog` | `builtin:<family>` 或自訂 catalog YAML 路徑 | `builtin:generic` |
| `default_command` | 預設 read-only command ID | `show_version` |
| `baudrate` | Serial baudrate | `115200` |
| `timeout` | SSH/Serial 共用 timeout | `15` |
| `ssh_timeout` | SSH timeout；優先於 `timeout` | `15` |
| `ssh_connect_attempts` | SSH connection 最大嘗試次數 | `3` |
| `ssh_retry_backoff_seconds` | 初始 retry 等待秒數，每次失敗加倍 | `1` |
| `serial_timeout` | Serial timeout；優先於 `timeout` | `15` |
| `report_dir` | redacted JSON report 目錄 | `reports/cli-tool` |

設定優先順序為「CLI 參數 > `node_target.cli` > 工具預設值」。若 `cli.username` 與 auth profile 帳號不同，必須同時設定 `cli.password_env` 或傳入 `--password-env`，避免帳號與密碼來源錯配。

`include_output`、`no_report` 等安全或輸出控制不從 config 自動啟用，仍須在每次執行時明確傳入 CLI 參數。

其他專案若沒有相容的 `config_loader`，可使用通用 target YAML：

```yaml
version: 1
targets:
  lab-ies:
    transport: serial
    serial_port: COM5
    username: admin
    password_env: CLI_TOOL_SERIAL_PASSWORD
    catalog: builtin:ies52xx
    default_command: show_version
```

執行方式：

```powershell
device-cli-smoke `
  --target-config configs/device_cli_targets.yaml `
  --target lab-ies
```

若不用本專案 config，也可以手動指定 host 與 username，密碼仍只從環境變數讀：

```powershell
$env:CLI_TOOL_SSH_PASSWORD="your-password"
.\.venv\Scripts\python.exe -m tools.cli_tool_readonly_smoke `
  --node-key node3 `
  --host 192.0.2.10 `
  --username admin
```

若使用 serial console，例如 Windows 的 `COM5`，同樣只從環境變數讀密碼：

```powershell
$env:CLI_TOOL_SERIAL_PASSWORD="your-password"
.\.venv\Scripts\python.exe -m tools.cli_tool_readonly_smoke `
  --transport serial `
  --serial-port COM5 `
  --username admin `
  --password-env CLI_TOOL_SERIAL_PASSWORD
```

預設行為：

- 使用 wheel 內建的 `builtin:generic`。
- 執行 `show_version`。
- 每次 invocation 只連線一台設備；SSH smoke transport 只使用一個 SSH session，serial transport 只開一個 serial connection。
- SSH 僅在 command 尚未送出前重試暫時性連線錯誤；Authentication failure 與 command 執行錯誤不重試。
- 使用 `--env-node` 時，從被使用專案的 `node_target.cli` 讀取 SSH/Serial 設定；未設定 `cli` 時維持 SSH、device IP 與 readwrite 帳密的既有行為。
- 只允許 catalog 內 `readonly: true` 的 command。
- 寫出 redacted JSON report 到 `reports/cli-tool/`。
- stdout 顯示 report path。
- 不輸出完整 CLI output。

已確認並加入 family catalog 的 read-only command：

| Catalog | Command ID | Command | 備註 |
| --- | --- | --- | --- |
| `configs/cli_tool/generic.yaml` | `show_version` | `show version` | 跨系列最小 smoke。 |
| `configs/cli_tool/neox.yaml` | `show_system_information` | `show system-information` | UG 已確認；尚未執行實機 smoke。 |
| `configs/cli_tool/ies52xx.yaml` | `show_system_information` | `show system-information` | IES4204 UG 已確認；尚未執行實機 smoke。 |

可選參數：

```powershell
--catalog builtin:generic
--command-id show_version
--transport ssh
--env-node node3
--target-config configs/device_cli_targets.yaml
--target lab-ies
--auth-profile default
--node-key node3
--host 192.0.2.10
--username admin
--password-env CLI_TOOL_SERIAL_PASSWORD
--ssh-connect-attempts 3
--ssh-retry-backoff-seconds 1
--serial-port COM5
--baudrate 115200
--serial-timeout 15
--report-dir reports/cli-tool
--no-report
--include-output
--max-output-chars 20000
--param key=value
```

`--include-output` 只應在確認 CLI output 無高風險敏感資訊時使用。工具會遮罩常見 password/token key，以及 raw output 中常見的 MAC、serial number、hostname、contact、location 欄位；但這不是完整資料防外洩保證。大型 output 會依 `--max-output-chars` 截斷後寫入 report。

## 實機 Smoke 前檢查清單

執行前需確認：

- 目標設備系列與 model，例如 NeoX-06、NeoX-02、IES52XX、OLT140X。
- `node-key` 是否清楚可辨識。
- 使用 SSH 時，device IP / host 是否正確。
- 使用 serial 時，COM port 是否正確，例如 `COM5`，且 console 參數為 115200 8N1、無 flow control。
- 若使用 `--env-node`，確認 `configs/test_targets.yaml` 的 `node.cli` 與 `configs/auth_accounts.yaml` 的 readwrite profile 可用。
- 若不用 `--env-node`，CLI username 是否為允許 read-only smoke 的帳號，且密碼只放在環境變數，例如 `CLI_TOOL_SSH_PASSWORD`。
- Serial 可搭配 `--env-node`；需在 node 的 `cli.serial_port` 設定 COM port，或用 `--serial-port` 覆寫。
- 本次只執行 read-only command。
- 不使用 `--include-output`，除非確認 output 無敏感資訊。
- 允許 report 寫入 `reports/cli-tool/`。

## Config / Mutating Command 邊界

目前不要把 CLI config command 放進 catalog，例如：

```text
configure
interface ...
vlan ...
delete ...
clear ...
save ...
```

原因：

- config command 會改設備狀態。
- 不同設備系列的 config mode、commit/save、rollback 行為不同。
- 需要 allowlist、dry-run、pre-check、post-check、cleanup 與人工確認。

後續若要支援，應新增獨立 workflow，例如：

```text
cli_tool/workflows/configure.py
cli_tool/safety/policy.py
```

並要求：

- 預設禁止 mutation。
- 必須明確傳入 `allow_mutation=True`。
- command 必須命中 allowlist。
- 必須產生 redacted transcript/report。
- 必須有 pre-check/post-check。
- 可清理的設定需提供 cleanup sequence。

## IES52XX / OLT140X 加入規則

加入新系列前，先確認：

- SSH shell 是否與 NeoX 相同。
- prompt 格式。
- 是否有 paging，例如 `--More--`。
- read-only show/display command 語法。
- config mode 進出方式。
- 是否有 yes/no confirm。
- firmware 版本對 output 欄位的影響。

第一批只建議加入低風險 read-only command，例如版本查詢。若命令尚未確認，先不要建立 catalog。

## Legacy FTP 安全模式

FTP/FTPS 預設要求 MLSD/MLST 結構化 metadata。舊伺服器只有在 invocation 同時指定下列兩個參數時，才允許嚴格 UNIX LIST fallback：

```powershell
--allow-legacy-listing
--legacy-list-format unix
```

fallback 只處理 FTP command unsupported；permission denied、未知格式、特殊檔案、unsafe basename 或 symlink download 仍立即失敗。工具不會只依 SIZE 推定 regular file，report 會記錄 fallback 是否啟用及實際使用的 listing/metadata method。

## 驗證命令

修改 Python 工具後至少跑：

```powershell
python -m compileall cli_tool tools\cli_tool_readonly_smoke.py
.\.venv\Scripts\python.exe -m pytest `
  tests\test_cli_tool_verify.py `
  tests\test_cli_tool_transport.py `
  tests\test_cli_tool_catalog.py `
  tests\test_cli_tool_catalog_loader.py `
  tests\test_cli_tool_driver_verify.py `
  tests\test_cli_tool_builtin_catalogs.py `
  tests\test_cli_tool_readonly_smoke.py
```

上述測試皆為離線或 mock 測試，不應連 EMS 或設備。


