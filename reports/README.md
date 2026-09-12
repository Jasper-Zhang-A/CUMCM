# 复现与交付入口

在项目根目录执行：

```bash
/home/jasper/miniconda3/envs/tslib/bin/python scripts/run_all.py --workers 3
```

统一入口依次执行单测、数据审计与问题一、35天跨月小样本、1月费用校准、全年四分支回测、消融、独立核验、统一CSV原子写入及报告。输入只读取 `data/data_clean.csv`。依赖版本见 `configs/requirements.txt`；本机指定Python环境已具备依赖。

分阶段复现可使用 `--stage smoke`、`calibrate`、`backtest`、`ablations`、`publish`、`reports`。后续阶段读取前面已经生成的cache；输入、代码或配置改变时应完整重跑，而非混用旧cache。

正式结果仅为 `data/results_template_clean.csv`。模型和结算口径见 `model.md`，独立核验见 `validation.md`，全部费用、消融和题目指定日期表格见 `comparison.md`，数据及CyclePatch借鉴见 `data_audit.md`、`cyclepatch.md`。

`cache/<策略>/trace.npz` 保存逐时执行量，`metadata.json` 保存逐次决策的信息时间、场景来源、概率、采用标记和运行参数，`daily.json` 是报告用诊断汇总。它们是复算缓存，不是另一份正式答案。`cache/backups/` 保存发布前CSV备份。`logs/` 记录测试、运行日志、环境哈希及发布审计。

报告保留完整精度计算结果，Markdown展示通常保留三位小数。CSV保留15位有效数字。再次完整运行相同输入和参数可重新核验并覆盖数值；如果改变模型使已追加的事件行不再适用，写入器会拒绝删除原行，应先审查模板与模型版本，而不是静默删除事件行。

本项目提供已核验的可执行近似策略，不宣称原多阶段随机优化的全局最优性。效率和价格结算存在题面解释空间，本次选择在模型报告中明确列示。
