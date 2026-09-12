# 独立核验报告

核验器不导入优化器、不复用求解器目标值；从原始执行轨迹独立重算供需平衡、状态递推、功率和互斥、跨日连续性、版本覆盖、结算恒等式及紧急事件。

| 结果 | 天数 | 最大平衡误差(kWh) | 最大递推误差(kWh) | 跨日误差(kWh) | 同充同放(kWh) | 版本误差(kWh) | 费用账本误差(元) | 通过 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| result1 | 1 | 1.137e-13 | 9.095e-13 | 0.000e+00 | 0.000e+00 | 0.000e+00 | 0.000e+00 | True |
| result2 | 365 | 4.547e-13 | 1.137e-13 | 0.000e+00 | 0.000e+00 | 0.000e+00 | 0.000e+00 | True |
| result3 | 365 | 2.274e-13 | 1.137e-13 | 0.000e+00 | 0.000e+00 | 0.000e+00 | 1.455e-11 | True |
| result4-2 | 365 | 4.547e-13 | 1.137e-13 | 0.000e+00 | 0.000e+00 | 0.000e+00 | 0.000e+00 | True |
| result4-3 | 365 | 2.274e-13 | 1.137e-13 | 0.000e+00 | 0.000e+00 | 0.000e+00 | 2.183e-11 | True |

## 信息时间审计

| 结果 | 决策数 | 发布时间检查 | 历史日期检查 | 概率检查 |
| --- | --- | --- | --- | --- |
| result1 | 0 | 0 | 0 | 0 |
| result2 | 1460 | 0 | 10132 | 1460 |
| result3 | 1460 | 1460 | 10132 | 1460 |
| result4-2 | 1460 | 0 | 10132 | 1460 |
| result4-3 | 1460 | 1460 | 10132 | 1460 |

全年的输入读取与决策可知性分离。历史特征、预测、残差和场景来源均早于决策日；光伏版本发布时间不晚于决策时刻。另有未来数据扰动测试检测预测和当前动作是否受不可见数据影响。日志时间戳检查不能单独证明程序不存在所有信息泄露，需结合代码边界和扰动测试。

## 统一CSV发布核验

```json
{
  "rows": 362908,
  "original_rows_preserved": 360880,
  "appended_emergency_rows": 2028,
  "status_counts": {
    "filled": 328807,
    "no_event": 549,
    "not_used": 33552
  },
  "column_count": 25,
  "immutable_metadata_preserved": true,
  "all_values_independently_recomputed": true,
  "trace_checks": {
    "result1": {
      "result_id": "result1",
      "passed": true,
      "days": 1,
      "first_day": "typical_day",
      "last_day": "typical_day",
      "tolerance_kwh": 1e-05,
      "max_balance_error_kwh": 1.1368683772161603e-13,
      "max_storage_error_kwh": 9.094947017729282e-13,
      "max_continuity_error_kwh": 0.0,
      "max_simultaneous_kwh": 0.0,
      "max_version_error_kwh": 0.0,
      "max_ledger_error_cny": 0.0,
      "max_event_error_kwh": 0.0,
      "information_audit": {
        "decisions": 0,
        "forecast_issue_checks": 0,
        "historical_day_checks": 0,
        "weight_checks": 0,
        "reveal_checks": 0,
        "solver_checks": 0
      },
      "total_cost_all_days": 33801.495542222016,
      "independent_of_optimizer_objective": true
    },
    "result2": {
      "result_id": "result2",
      "passed": true,
      "days": 365,
      "first_day": "2025-01-01",
      "last_day": "2025-12-31",
      "tolerance_kwh": 1e-05,
      "max_balance_error_kwh": 4.547473508864641e-13,
      "max_storage_error_kwh": 1.1368683772161603e-13,
      "max_continuity_error_kwh": 0.0,
      "max_simultaneous_kwh": 0.0,
      "max_version_error_kwh": 0.0,
      "max_ledger_error_cny": 0.0,
      "max_event_error_kwh": 2.504130236502533e-11,
      "information_audit": {
        "decisions": 1460,
        "forecast_issue_checks": 0,
        "historical_day_checks": 10132,
        "weight_checks": 1460,
        "reveal_checks": 1460,
        "solver_checks": 2920
      },
      "total_cost_all_days": 15691637.39004063,
      "independent_of_optimizer_objective": true
    },
    "result3": {
      "result_id": "result3",
      "passed": true,
      "days": 365,
      "first_day": "2025-01-01",
      "last_day": "2025-12-31",
      "tolerance_kwh": 1e-05,
      "max_balance_error_kwh": 2.2737367544323206e-13,
      "max_storage_error_kwh": 1.1368683772161603e-13,
      "max_continuity_error_kwh": 0.0,
      "max_simultaneous_kwh": 0.0,
      "max_version_error_kwh": 0.0,
      "max_ledger_error_cny": 1.4551915228366852e-11,
      "max_event_error_kwh": 2.5158541916425747e-11,
      "information_audit": {
        "decisions": 1460,
        "forecast_issue_checks": 1460,
        "historical_day_checks": 10132,
        "weight_checks": 1460,
        "reveal_checks": 1460,
        "solver_checks": 2920
      },
      "total_cost_all_days": 15025861.650502257,
      "independent_of_optimizer_objective": true
    },
    "result4-2": {
      "result_id": "result4-2",
      "passed": true,
      "days": 365,
      "first_day": "2025-01-01",
      "last_day": "2025-12-31",
      "tolerance_kwh": 1e-05,
      "max_balance_error_kwh": 4.547473508864641e-13,
      "max_storage_error_kwh": 1.1368683772161603e-13,
      "max_continuity_error_kwh": 0.0,
      "max_simultaneous_kwh": 0.0,
      "max_version_error_kwh": 0.0,
      "max_ledger_error_cny": 0.0,
      "max_event_error_kwh": 2.6147972675971687e-11,
      "information_audit": {
        "decisions": 1460,
        "forecast_issue_checks": 0,
        "historical_day_checks": 10132,
        "weight_checks": 1460,
        "reveal_checks": 1460,
        "solver_checks": 2920
      },
      "total_cost_all_days": 16779128.415163074,
      "independent_of_optimizer_objective": true
    },
    "result4-3": {
      "result_id": "result4-3",
      "passed": true,
      "days": 365,
      "first_day": "2025-01-01",
      "last_day": "2025-12-31",
      "tolerance_kwh": 1e-05,
      "max_balance_error_kwh": 2.2737367544323206e-13,
      "max_storage_error_kwh": 1.1368683772161603e-13,
      "max_continuity_error_kwh": 0.0,
      "max_simultaneous_kwh": 0.0,
      "max_version_error_kwh": 0.0,
      "max_ledger_error_cny": 2.1827872842550278e-11,
      "max_event_error_kwh": 2.4719781777093885e-11,
      "information_audit": {
        "decisions": 1460,
        "forecast_issue_checks": 1460,
        "historical_day_checks": 10132,
        "weight_checks": 1460,
        "reveal_checks": 1460,
        "solver_checks": 2920
      },
      "total_cost_all_days": 16063705.083400894,
      "independent_of_optimizer_objective": true
    }
  },
  "original_sha256": "d7f15d4d14f7546ca96a36838823ef289ee2292a631f76b99445de416dc8fe6d",
  "output_sha256": "e8251494c1ff83182354c1276d7b7432147bbd26018cdaa98a54771dd2deabad",
  "backup": "/mnt/c/Users/jaspe/Desktop/CUMCM/cache/backups/results_template_clean.20260912T105201794278Z.d7f15d4d14f7.csv",
  "atomic_replacement": true,
  "complete_year_required": true,
  "source_alignment_audit": {
    "passed": true,
    "input_path": "/mnt/c/Users/jaspe/Desktop/CUMCM/data/data_clean.csv",
    "input_sha256": "89d65b8c047b6738d1b8db8f070bf73ecad92cad12de9341efb0c1fa5577891e",
    "input_rows": 193152,
    "independently_parsed_interval_rows": 158112,
    "max_absolute_error": 2.2737367544323206e-13,
    "comparisons": {
      "result1": {
        "load": {
          "dataset_id": "A1_LOAD",
          "compared_values": 144,
          "max_absolute_error": 1.1368683772161603e-13
        },
        "pv": {
          "dataset_id": "A1_PV_FORECAST",
          "compared_values": 144,
          "max_absolute_error": 2.2737367544323206e-13
        },
        "price": {
          "dataset_id": "A1_PRICE",
          "compared_values": 144,
          "max_absolute_error": 0.0
        }
      },
      "result2": {
        "load": {
          "dataset_id": "A2_LOAD",
          "compared_values": 52560,
          "max_absolute_error": 2.2737367544323206e-13
        },
        "pv": {
          "dataset_id": "A2_PV_ACTUAL",
          "compared_values": 52560,
          "max_absolute_error": 2.2737367544323206e-13
        },
        "price": {
          "dataset_id": "A1_PRICE",
          "compared_values": 52560,
          "max_absolute_error": 0.0
        }
      },
      "result3": {
        "load": {
          "dataset_id": "A2_LOAD",
          "compared_values": 52560,
          "max_absolute_error": 2.2737367544323206e-13
        },
        "pv": {
          "dataset_id": "A2_PV_ACTUAL",
          "compared_values": 52560,
          "max_absolute_error": 2.2737367544323206e-13
        },
        "price": {
          "dataset_id": "A1_PRICE",
          "compared_values": 52560,
          "max_absolute_error": 0.0
        }
      },
      "result4-2": {
        "load": {
          "dataset_id": "A2_LOAD",
          "compared_values": 52560,
          "max_absolute_error": 2.2737367544323206e-13
        },
        "pv": {
          "dataset_id": "A2_PV_ACTUAL",
          "compared_values": 52560,
          "max_absolute_error": 2.2737367544323206e-13
        },
        "price": {
          "dataset_id": "A4_PRICE",
          "compared_values": 52560,
          "max_absolute_error": 0.0
        }
      },
      "result4-3": {
        "load": {
          "dataset_id": "A2_LOAD",
          "compared_values": 52560,
          "max_absolute_error": 2.2737367544323206e-13
        },
        "pv": {
          "dataset_id": "A2_PV_ACTUAL",
          "compared_values": 52560,
          "max_absolute_error": 2.2737367544323206e-13
        },
        "price": {
          "dataset_id": "A4_PRICE",
          "compared_values": 52560,
          "max_absolute_error": 0.0
        }
      }
    },
    "method": "independent typed CSV reread; kW divided by 6; dataset/day/slot alignment"
  }
}
```

正式CSV仅在完整日历、轨迹、信息时间、结算和模板结构核验通过后原子替换。保留原字段、行序、record_id及来源元数据；只填写允许字段，并按实际需要追加紧急事件。写入前备份路径见上方发布记录。

## 测试与可复现性

使用指定Python环境运行 `python -m unittest discover -s tests -v`。测试覆盖经济分位数、100→80结算、连续版本账本、采用但不变、未采用、无事件/多事件/跨日、LP互斥修正、MILP一致性、储能机会价值、因果预测和模板元数据保护。具体本次测试输出见 `logs/tests.log`，环境和源码哈希见 `logs/run_manifest.json`。

## 完成范围及局限

五项主结果覆盖模板要求时段，四个随机分支均有完整1月预热和365天连续执行轨迹。消融完整覆盖同一输出区间。尚未提供原多阶段随机问题的全局最优性证书、跨年度泛化保证或效率口径的全参数敏感性扫描；这些不应误写为已完成。未导出XLSX，正式交付以用户指定统一CSV为准。
