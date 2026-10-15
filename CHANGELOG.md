# Changelog

## 0.6.0

- 新增 `device-cli-xpon-plan`，產生 NeoX xPON register-method A 的唯讀 dry-run HTML/JSON。
- Node、IP、Slot、Port 必須精確確認，mutation sequence 採固定 allowlist。
- `--run-mutating` 在原始狀態讀取與 rollback 尚未驗證前固定拒絕，絕不連線或修改設備。
- 新增離線安全測試，包含 command injection、目標錯誤、live lock 與敏感資料檢查。

## 0.1.1

- SSH 在送出 command 前遇到暫時性連線或 handshake 錯誤時，支援可設定的指數退避重試。
- Authentication failure 與 command 執行錯誤維持不重試，避免鎖定帳號或重複執行命令。
- `device-cli-smoke` 新增 `--ssh-connect-attempts` 與 `--ssh-retry-backoff-seconds`。
- Portable target config 新增 `ssh_connect_attempts` 與 `ssh_retry_backoff_seconds`。

## 0.1.0

- 初始公開版本。
- 支援單台設備的 SSH 或 Serial read-only smoke 驗證。
- 支援可攜式 target config、內建 command catalog 與 redacted JSON report。
