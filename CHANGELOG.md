# Changelog

## 0.7.0

- 新增 `device-cli-netconf-test`(optional `netconf` extra,依賴 `ncclient`),支援 NETCONF `connect` 連線測試與唯讀 `get`/`get-config`,已對實機 NeoX NXC400 驗證。
- `edit-config` 預設仍是 dry-run 計畫、不連線;加上 `--run-mutating` 才會真的執行,永遠固定走 `candidate` datastore(不接受直接寫 `running`),依序 `lock`→`edit-config`→`validate`→`commit(confirmed)`→(選用)`verify`→`unlock`,失敗時自動 `discard-changes`。預設不送確認 commit,`--confirm-timeout-seconds`(預設 30,限制 5–600)一到裝置會自動把 `running` 還原;要讓變更永久生效需明確加 `--finalize-commit`。
- `execute_confirmed_commit()` 在送出 `lock`/`edit-config` 前檢查 server capabilities,缺 `:candidate` 或 `:confirmed-commit`(1.0/1.1)即拋 `NetconfCapabilityError` 並拒絕執行,不會退回直接寫 `running`;CLI report 以 `result: REFUSED_MISSING_CAPABILITY` 與 `missing_capabilities` 列出缺少項目。
- 新增真實 `ncclient` import smoke test(只 import/建構,不連線),確認與 ncclient 0.7.x 相容。
- `docs/netconf.md` 新增「External integration」:NeoxAutoTest/EMS web auto 引用方式、`NetconfClient` 最小用法與版本相容承諾。
- NETCONF host-key 驗證改為自行以 SHA256 指紋 pin(`--host-key-fingerprint`),不再依賴 `ncclient` 內建的 `hostkey_verify`——後者會用 known_hosts 裡的金鑰型別覆寫 paramiko 的協商清單,對 RSA host key 會意外鎖進 paramiko 5.x 已移除的 `ssh-rsa` 簽章,導致本可正常運作的連線失敗。
- `cli_tool/reporting/redaction.py` 新增 XML tag 型敏感資訊遮蔽(`<password>`、`<community>`、`<pre-shared-key>`、`<public-key>`、`<private-key>`、`<cleartext-private-key>` 等),涵蓋 `--include-output` 收錄的 NETCONF 原始 XML,包含 `ietf-keystore` 金鑰資料。

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
