# 跨專案整合指南

## 安裝

正式發佈至 GitHub 後，可在其他專案的虛擬環境安裝：

```powershell
python -m pip install "device-cli-tool @ git+https://github.com/sourceKang/device-cli-tool.git@main"
device-cli-smoke --help
```

若專案需要固定版本，請改用 release tag 或 commit SHA，不要永久依賴浮動的 `main`。

## 通用 Target Config

沒有相容 `config_loader` 的專案，可建立自己的 target YAML。格式可參考 `examples/device_cli_targets.example.yaml`：

```yaml
version: 1
targets:
  lab-ies:
    transport: serial
    serial_port: COM5
    baudrate: 115200
    timeout: 15
    username: admin
    password_env: CLI_TOOL_SERIAL_PASSWORD
    catalog: builtin:ies52xx
    default_command: show_version
```

密碼只放在環境變數：

```powershell
$env:CLI_TOOL_SERIAL_PASSWORD="your-password"
device-cli-smoke `
  --target-config configs/device_cli_targets.yaml `
  --target lab-ies
```

設定優先順序為「CLI 參數 > target config > 工具預設值」。`--include-output` 與 `--no-report` 不會由 target config 自動開啟。

## Optional config_loader Adapter

若被使用專案提供 `config_loader.load_environment()`，可直接使用：

```powershell
device-cli-smoke --env-node NODE1 --auth-profile default
```

工具會讀取 `EnvironmentConfig.node_target.cli`。若 node 沒有 `cli` 區段，維持既有 SSH、`device_ip` 與 readwrite credential 行為。

## Catalog

wheel 內建以下 read-only catalog：

- `builtin:generic`
- `builtin:neox`
- `builtin:ies52xx`
- `builtin:olt140x`

也可用 `--catalog path/to/custom.yaml` 載入被使用專案自己的 read-only catalog。自訂 catalog 不得包含密碼、token、設備 IP 或 mutating command。

## 更新與移除

從 GitHub 更新：

```powershell
python -m pip install --upgrade --force-reinstall "device-cli-tool @ git+https://github.com/sourceKang/device-cli-tool.git@main"
```

移除：

```powershell
python -m pip uninstall device-cli-tool
```
