# StarRocks TV Alert Scripts

仓库当前包含三条彼此独立的 TV 告警脚本：

- `alert/manage_model_global_pl_monitor_alert.py`
  统计 `fin_global.manage_model_global_pl_monitor` 最新 `etl_create_time` 批次的总记录数和 `diff <> 0` 异常记录数，按摘要告警格式发送到 TV 机器人。
- `alert/fin_manage_ods_data_quality_monitor_alert.py`
  统计 `fin.fin_manage_ods_data_quality_monitor` 的总记录数与 `diff <> 0` 的异常记录数，按“数仓与财务库数据一致性校验”格式发送到 TV 机器人。
- `alert/mx_capital_ltv_alert.py`
  分别查询墨西哥 `dm_dd_new.ads_capital_ltv` 中 `new_share` 与 `chuanjin` 自 `2026-05-01` 起的最新资方 LTV、账户余额和质押正常在贷，按资方阈值生成“墨西哥资方ltv告警”。
- `alert/id_marketing_dwd_table_cnt_alert.py`
  刷新 `testdb.test_dwd_ad_table_cnt_check` 中印尼投放 DWD 表 T-1 分区数据量校验结果，并逐条列出 `cnt <= 0` 或缺失校验结果的问题表。

## 运行

原有 PL 监控告警：

```bash
python3 alert/manage_model_global_pl_monitor_alert.py \
  --sr-password '主账号密码' \
  --sr-backup-password '备份账号密码' \
  --bot-id '4d0bcc9b-71bf-41c5-ba9f-89b7278f9214' \
  --mentions 'adamyu@kn.group,gretchenhe@kn.group'
```

新增数仓与财务库数据一致性校验告警：

```bash
python3 alert/fin_manage_ods_data_quality_monitor_alert.py \
  --sr-password '主账号密码' \
  --sr-backup-password '备份账号密码' \
  --bot-id '4d0bcc9b-71bf-41c5-ba9f-89b7278f9214' \
  --mentions 'adamyu@kn.group,gretchenhe@kn.group'
```

墨西哥资方 LTV 告警：

```bash
SR_HOST='墨西哥StarRocks地址' \
SR_PORT='9030' \
SR_DB='dm_dd_new' \
SR_USERNAME='e_load' \
SR_BACKUP_USERNAME='backup_user' \
python3 alert/mx_capital_ltv_alert.py \
  --sr-password '主账号密码' \
  --sr-backup-password '备份账号密码' \
  --bot-id '5d0be3c3-0e06-4134-bbbe-690d7ff28d1e' \
  --mentions 'owner@kn.group,backup@kn.group'
```

印尼投放 DWD 表产出校验告警：

```bash
SR_HOST='印尼StarRocks地址' \
SR_PORT='9030' \
SR_DB='testdb' \
SR_USERNAME='e_load' \
SR_BACKUP_USERNAME='backup_user' \
python3 alert/id_marketing_dwd_table_cnt_alert.py \
  --sr-password '主账号密码' \
  --sr-backup-password '备份账号密码' \
  --bot-id '印尼投放告警机器人ID' \
  --mentions 'owner@kn.group,backup@kn.group'
```

只预览不发送：

```bash
python3 alert/manage_model_global_pl_monitor_alert.py \
  --sr-password '主账号密码' \
  --sr-backup-password '备份账号密码' \
  --dry-run
```

```bash
python3 alert/fin_manage_ods_data_quality_monitor_alert.py \
  --sr-password '主账号密码' \
  --sr-backup-password '备份账号密码' \
  --dry-run
```

```bash
python3 alert/mx_capital_ltv_alert.py \
  --sr-password '主账号密码' \
  --sr-backup-password '备份账号密码' \
  --target-date '2026-06-21' \
  --dry-run
```

```bash
python3 alert/id_marketing_dwd_table_cnt_alert.py \
  --sr-password '主账号密码' \
  --sr-backup-password '备份账号密码' \
  --target-date '2026-07-01' \
  --dry-run
```

## 配置

默认配置：

- StarRocks: `nlb-ngj6e0efsvv7wm73v3.cn-shanghai.nlb.aliyuncsslb.com:9031`
- PL 监控默认 DB: `ods`
- 数据一致性校验默认 DB: `fin`
- 主账号: `e_load`
- 备份账号: `e_backup`
- TV Bot: `f82292a5-45c5-42ea-84da-272b4c81ebcc`
- 默认 @ 人: `adamyu@kn.group,gretchenhe@kn.group`
- 数据一致性校验默认 @ 人: `adamyu@kn.group,gretchenhe@kn.group`
- 墨西哥资方 LTV 默认 DB: `dm_dd_new`
- 墨西哥资方 LTV 默认主账号: `e_load`
- 墨西哥资方 LTV 默认备份账号: `backup_user`
- 印尼投放 DWD 表校验默认 DB: `testdb`
- 印尼投放 DWD 表校验默认主账号: `e_load`
- 印尼投放 DWD 表校验默认备份账号: `backup_user`

可通过命令行参数覆盖默认值，例如任务传参 `--mentions 'owner@kn.group,backup@kn.group'`。也可通过环境变量覆盖：`SR_HOST`、`SR_PORT`、`SR_DB`、`SR_USERNAME`、`SR_BACKUP_USERNAME`、`TV_API_URL`、`MANAGE_MODEL_GLOBAL_PL_TV_BOT_ID`、`MANAGE_MODEL_GLOBAL_PL_TV_MENTIONS`、`FIN_MANAGE_ODS_DATA_QUALITY_TV_BOT_ID`、`FIN_MANAGE_ODS_DATA_QUALITY_TV_MENTIONS`。
墨西哥资方 LTV 还可通过 `MX_CAPITAL_LTV_TV_BOT_ID`、`MX_CAPITAL_LTV_TV_MENTIONS` 覆盖 TV 机器人和默认 @ 人。
印尼投放 DWD 表校验还可通过 `ID_MARKETING_DWD_TABLE_CNT_TV_BOT_ID`、`ID_MARKETING_DWD_TABLE_CNT_TV_MENTIONS` 覆盖 TV 机器人和默认 @ 人。

## n8n 接入

参考 `n8n/mx_new_share_ltv_alert_workflow.json` 与 `n8n/mx_chuanjin_ltv_alert_workflow.json`。设计与现有 PL 告警一致：

1. `Webhook` 触发，新分享路径 `MX_NEW_SHARE_LTV`，串金路径 `MX_CHUANJIN_LTV`。
2. `LTV告警代码拉取` 下载 `chenjiuxuan1/starrocks-ltv-mx-tv-alert` 的 GitHub main 分支到跳板机 `/root/starrocks-ltv-mx-tv-alert`。
3. `新分享LTV告警触发` 执行 `python3 alert/mx_capital_ltv_alert.py --capital new_share`，发送“墨西哥新分享ltv”通知。
4. `串金LTV告警触发` 执行 `python3 alert/mx_capital_ltv_alert.py --capital chuanjin`，发送“墨西哥串金ltv”通知。

模板已按“智能告警修复-墨西哥”的 n8n 配置写入墨西哥跳板机 `172.20.220.165`、SSH 凭据 `7oQDoS8H2buTjr7H / 墨西哥跳板机`、墨西哥 SR 连接信息和默认 @ 人 `liorawu@kn.group`；TV Bot 固定为 `5d0be3c3-0e06-4134-bbbe-690d7ff28d1e`。

印尼投放 DWD 表产出校验参考 `n8n/id_marketing_dwd_table_cnt_alert_workflow.json`：

1. `Webhook` 触发路径 `ID_MARKETING_DWD_CNT`。
2. `投放DWD告警代码拉取` 下载 `chenjiuxuan1/starrocks-pl-monitor-tv-alert` 的 GitHub main 分支到印尼跳板机 `/root/starrocks-pl-monitor-tv-alert`。
3. `投放DWD告警触发` 执行 `python3 alert/id_marketing_dwd_table_cnt_alert.py`，脚本先创建/刷新 `testdb.test_dwd_ad_table_cnt_check`，再把每一个 `cnt <= 0` 或缺失校验结果的 DWD 表逐条发送到 TV。
4. 模板已使用印尼跳板机 `192.168.21.236` 和 SSH 凭据 `SyHW5nXUCnuULr2c / 印尼跳板机`；SR 地址、密码、TV Bot 和 @ 人需要在导入 n8n 后替换占位值。
