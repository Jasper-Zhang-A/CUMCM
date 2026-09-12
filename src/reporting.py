"""Generate all numeric comparisons directly from verified execution traces."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from .simulation import load_trace
from .verification import summarize, verify_trace

MAIN = ('result1','result2','result3','result4-2','result4-3')
DATES = ('2025-03-20','2025-06-21','2025-09-23','2025-12-21')


def table(headers, rows):
    def cell(x):
        return f'{x:.3f}' if isinstance(x, (float, np.floating)) else str(x)
    return '\n'.join(['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(cell,row))+' |' for row in rows])+'\n'


def totals(trace):
    ds = [x for x in summarize(trace) if x['day_id']=='typical_day' or x['day_id'] >= '2025-02-01']
    out = {k: sum(d[k] for d in ds) for k in ('planned_cost','planned_settled','adjustment_fee','emergency_cost',
           'total_cost','emergency_kwh','curtailment_kwh','charge_kwh','discharge_kwh','planned_kwh','effective_kwh')}
    out.update(initial_energy=ds[0]['initial_energy'], final_energy=ds[-1]['final_energy'],
               days=len(ds), runtime=trace['metadata']['runtime_seconds'], events=sum(len(x['events']) for x in ds))
    return out


def generate_reports(root):
    root = Path(root)
    names = list(MAIN)+['q2_yesterday','q2_lastweek','q2_independent_errors','q2_no_week_alignment',
        'q2_static_weights','q2_greedy_storage','q3_midnight_forecast','q3_new_forecast_fixed_plan',
        'q4_static_weights','q4_independent_price']
    traces = {name:load_trace(root/'cache'/name) for name in names if (root/'cache'/name/'trace.npz').exists()}
    total = {name:totals(trace) for name,trace in traces.items()}
    validation = {}
    for name in MAIN:
        if name in traces:
            validation[name] = verify_trace(traces[name], name)
    (root/'reports/computed_totals.json').write_text(json.dumps(total,ensure_ascii=False,indent=2),encoding='utf-8')
    comparison = ['# 本次计算结果、基线与消融\n',
        '全部数值由本次逐十分钟因果执行轨迹重算；问题二至四统计2025-02-01至12-31，共334天，1月仅用于预热和历史校准。费用单位为元，电量单位为kWh。附件1固定价格与附件4随机价格属于不同结算环境，不能直接据两个环境费用高低判断方法优劣。\n',
        '## 五项正式结果\n', table(['结果','天数','实际总费用','计划结算(含调整)','其中调整费','紧急费','紧急电量','弃余电量','期初库存','期末库存','运行秒'],
            [[n,total[n]['days'],*[total[n][k] for k in ('total_cost','planned_settled','adjustment_fee','emergency_cost',
               'emergency_kwh','curtailment_kwh','initial_energy','final_energy','runtime')]] for n in MAIN if n in total])]
    if 'result1' in traces:
        meta=traces['result1']['metadata']
        comparison += [f"问题一LP费用为{meta['lp_objective']:.6f}元，MILP费用为{meta['milp_objective']:.6f}元，绝对差{abs(meta['lp_objective']-meta['milp_objective']):.8f}元。\n"]
    calpath=root/'cache/calibration.json'
    if calpath.exists():
        cal=json.loads(calpath.read_text())
        comparison += ['## 只用1月的费用校准\n',
            '候选从1月1日连续运行，前15天统一默认预热，1月16日从相同库存启用候选，以1月16—31日实付费用选择，参数在2月1日生效。1月正式预热使用固定默认参数，没有反向替换预热轨迹。末端库存单独列出；这些是有限候选的费用比较，不是总体统计最优的证明。\n']
        for family,value in cal.items():
            comparison += [f"### {family}：选择 `{value['selected']}`\n", table(['模式','带宽','验证实际费','验证起始库存','验证期末库存'],
                [[r['candidate']['mode'],r['candidate']['bandwidth'],r['validation_cost_cny'],r['initial_energy_kwh'],r['final_energy_kwh']]
                 for r in value['candidates']])]
    comparison += ['## 消融：同一信息及结算环境下的配对比较\n',
        '同一分支的1月预热相同；只在2月以后改变对应因素。正费用差表示实验策略比对照更贵，负数表示该次回测费用较低。期末库存同时列出，未折现或虚构售电收入。\n']
    pairs=[('昨日预测','result2','q2_yesterday'),('上周预测','result2','q2_lastweek'),
           ('独立时段/变量误差','q2_static_weights','q2_independent_errors'),
           ('取消星期对齐','result2','q2_no_week_alignment'),('均匀权重','result2','q2_static_weights'),
           ('缺口优先放电','result2','q2_greedy_storage'),
           ('新增预报且保持计划','q3_midnight_forecast','q3_new_forecast_fixed_plan'),
           ('允许经济修订','q3_new_forecast_fixed_plan','result3'),
           ('独立价格路径','q4_static_weights','q4_independent_price')]
    rows=[]
    for label,a,b in pairs:
        if a in total and b in total:
            rows.append([label,a,b,total[b]['total_cost']-total[a]['total_cost'],
                         total[a]['initial_energy'],total[b]['initial_energy'],total[a]['final_energy'],total[b]['final_energy']])
    comparison += [table(['实验因素','对照','实验','实验−对照费用','对照期初E','实验期初E','对照期末E','实验期末E'],rows),
        table(['策略','实际总费用','紧急电量','调整费','弃余电量','期末库存','运行秒'],
            [[n,*[total[n][k] for k in ('total_cost','emergency_kwh','adjustment_fee','curtailment_kwh','final_energy','runtime')]]
             for n in names if n in total and n not in ('result1',)])]
    if all(n in total for n in ('result2','q2_no_week_alignment')):
        a,b=total['result2'],total['q2_no_week_alignment']
        comparison += [f"取消星期场景筛选相位惩罚后，全年费用减少{a['total_cost']-b['total_cost']:.3f}元，"
            f"其中计划结算减少{a['planned_settled']-b['planned_settled']:.3f}元、紧急费减少{a['emergency_cost']-b['emergency_cost']:.3f}元；"
            f"紧急电量减少{a['emergency_kwh']-b['emergency_kwh']:.3f} kWh，弃余减少{a['curtailment_kwh']-b['curtailment_kwh']:.3f} kWh。"
            f"期初库存相同，取消对齐者期末还多{b['final_energy']-a['final_energy']:.3f} kWh，因此这项费用差没有依赖耗尽更多末端库存。\n",
            '这一实验保留星期特征预测中心，仅改变历史场景筛选中的相位惩罚。负荷中心已经吸收部分星期规律；最多6条场景时，额外相位惩罚可能排除当前条件更相似的误差路径，光伏误差也未必具有相同星期周期。这是与结果相容的解释，尚非已验证的机制；不能由一个年份推断星期对齐普遍无效。主策略仍保留事前声明的结构，全年消融结果仅用于评估。\n']
        da={x['day_id']:x for x in summarize(traces['result2'])}
        db={x['day_id']:x for x in summarize(traces['q2_no_week_alignment'])}
        comparison += [table(['月份','取消星期对齐−主方案实际费用(元)'],
            [[f'2025-{month:02d}',sum(db[d]['total_cost']-v['total_cost'] for d,v in da.items() if d.startswith(f'2025-{month:02d}'))]
             for month in range(2,13)])]
    if all(n in total for n in ('q3_midnight_forecast','q3_new_forecast_fixed_plan','result3')):
        a,b,c=(total[n] for n in ('q3_midnight_forecast','q3_new_forecast_fixed_plan','result3'))
        comparison += [f"在相同零点光伏信息下，仅增加日内预报而保持购电版本，费用变化为{b['total_cost']-a['total_cost']:+.3f}元，"
            f"紧急电量变化{b['emergency_kwh']-a['emergency_kwh']:+.3f} kWh。费用和电量方向可能不同，因为机会价值控制关心发生缺口时的价格，而非只最小化缺电量。"
            f"再允许经济修订，费用变化{c['total_cost']-b['total_cost']:+.3f}元，其中绝对修订手续费{c['adjustment_fee']:.3f}元，"
            f"紧急费变化{c['emergency_cost']-b['emergency_cost']:+.3f}元。三者期末库存见配对表。\n"]
    if all(n in total for n in ('q4_static_weights','q4_independent_price')):
        a,b=(total[n] for n in ('q4_static_weights','q4_independent_price'))
        comparison += [f"在均匀权重的配对实验中，打乱完整价格路径与供需路径的配对后，费用变化{b['total_cost']-a['total_cost']:+.3f}元；"
            f"计划结算变化{b['planned_settled']-a['planned_settled']:+.3f}元、紧急费变化{b['emergency_cost']-a['emergency_cost']:+.3f}元，"
            f"期末库存变化{b['final_energy']-a['final_energy']:+.3f} kWh。"
            '本次没有观察到保留联合相关必然降低实付费用。联合误差是风险描述的建模依据，其有限历史估计、6场景压缩和近似控制仍有误差；'
            '这组一次固定错配实验不等于证明真实价格与供需独立，也不支持普遍舍弃联合建模。\n']
    for a,b in [('result2','result3'),('result4-2','result4-3')]:
        if a in total and b in total:
            diff=total[b]['total_cost']-total[a]['total_cost']
            comparison += [f"{b}相对{a}的实际总费用差为{diff:+.3f}元；两者末端库存分别为{total[b]['final_energy']:.3f}和{total[a]['final_energy']:.3f} kWh。这是所实现策略在该年的结果，不是新增信息必然带来相同收益的理论结论。\n"]
    comparison += ['预测、场景压缩、两阶段追索和储能网格均有近似误差。若某项改进回测更贵，应结合紧急量、调整费、弃余及库存差解释；没有将其归因于“信息有害”，也没有预设创新方法一定获胜。\n',
                   '## 题目指定日期与时段表\n','以下表1是零点计划；对可修订问题另列实际有效计划，以免把初始和修订版本相加。\n']
    for name in MAIN:
        if name not in traces: continue
        trace=traces[name]
        daily={x['day_id']:x for x in summarize(trace)}
        for day in (('typical_day',) if name=='result1' else DATES):
            i=trace['dates'].index(day); d=daily[day]
            comparison += [f'### {name} / {day}\n',table(['区间/指标','初始计划值','有效计划值','单位'],
                [[f'{h:02d}:00—{h:02d}:10',trace['q0'][i,h*6],trace['q'][i,h*6],'kWh'] for h in (10,12,14,16,18,20)]+
                [['全天购电量',d['planned_kwh'],d['effective_kwh'],'kWh'],['全天计划结算费',d['planned_cost'],d['planned_settled'],'元']]),
                table(['储能区间','充电量','放电量'],[[f'{h:02d}:00—{h+4:02d}:00',float(trace['c'][i,h*6:(h+4)*6].sum()),float(trace['d'][i,h*6:(h+4)*6].sum())] for h in range(0,24,4)]),
                f"0:00内部储电量：{d['initial_energy']:.3f} kWh；24:00：{d['final_energy']:.3f} kWh；实际总费用（含紧急）：{d['total_cost']:.3f}元。\n"]
            if name!='result1':
                def fmt(m): return f'{m//60:02d}:{m%60:02d}'
                comparison += [table(['紧急事件','时间段','紧急购电量(kWh)'],[[e['event_index'],fmt(e['start_minute'])+'—'+fmt(e['end_minute']),e['value']] for e in d['events']]) if d['events'] else '当日无紧急购电事件。\n']
    (root/'reports/comparison.md').write_text('\n'.join(comparison),encoding='utf-8')
    publication={}
    if (root/'logs/publication.json').exists(): publication=json.loads((root/'logs/publication.json').read_text())
    report=['# 独立核验报告\n','核验器不导入优化器、不复用求解器目标值；从原始执行轨迹独立重算供需平衡、状态递推、功率和互斥、跨日连续性、版本覆盖、结算恒等式及紧急事件。\n',
        table(['结果','天数','最大平衡误差(kWh)','最大递推误差(kWh)','跨日误差(kWh)','同充同放(kWh)','版本误差(kWh)','费用账本误差(元)','通过'],
            [[n,v['days'],*[f'{v[k]:.3e}' for k in ('max_balance_error_kwh','max_storage_error_kwh','max_continuity_error_kwh','max_simultaneous_kwh','max_version_error_kwh','max_ledger_error_cny')],v['passed']] for n,v in validation.items()]),
        '## 信息时间审计\n',table(['结果','决策数','发布时间检查','历史日期检查','概率检查'],
            [[n,*[v['information_audit'][k] for k in ('decisions','forecast_issue_checks','historical_day_checks','weight_checks')]] for n,v in validation.items()]),
        '全年的输入读取与决策可知性分离。历史特征、预测、残差和场景来源均早于决策日；光伏版本发布时间不晚于决策时刻。另有未来数据扰动测试检测预测和当前动作是否受不可见数据影响。日志时间戳检查不能单独证明程序不存在所有信息泄露，需结合代码边界和扰动测试。\n',
        '## 统一CSV发布核验\n', '```json\n'+json.dumps(publication,ensure_ascii=False,indent=2)+'\n```\n',
        '正式CSV仅在完整日历、轨迹、信息时间、结算和模板结构核验通过后原子替换。保留原字段、行序、record_id及来源元数据；只填写允许字段，并按实际需要追加紧急事件。写入前备份路径见上方发布记录。\n',
        '## 测试与可复现性\n','使用指定Python环境运行 `python -m unittest discover -s tests -v`。测试覆盖经济分位数、100→80结算、连续版本账本、采用但不变、未采用、无事件/多事件/跨日、LP互斥修正、MILP一致性、储能机会价值、因果预测和模板元数据保护。具体本次测试输出见 `logs/tests.log`，环境和源码哈希见 `logs/run_manifest.json`。\n',
        '## 完成范围及局限\n','五项主结果覆盖模板要求时段，四个随机分支均有完整1月预热和365天连续执行轨迹。消融完整覆盖同一输出区间。尚未提供原多阶段随机问题的全局最优性证书、跨年度泛化保证或效率口径的全参数敏感性扫描；这些不应误写为已完成。未导出XLSX，正式交付以用户指定统一CSV为准。\n']
    (root/'reports/validation.md').write_text('\n'.join(report),encoding='utf-8')
    if all(n in traces for n in ('result2','result3','result4-2','result4-3')):
        import os
        os.environ.setdefault('MPLCONFIGDIR', str(root.resolve()/'cache/matplotlib'))
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(1,2,figsize=(11,4),constrained_layout=True)
        for ax,pair,title in zip(axes,[('result2','result3'),('result4-2','result4-3')],['Fixed tariff','Unknown future prices']):
            for name in pair:
                ds=[x for x in summarize(traces[name]) if x['day_id']>='2025-02-01']
                ax.plot(np.arange(1,len(ds)+1),np.cumsum([x['total_cost'] for x in ds])/1e6,label=name)
            ax.set(title=title,xlabel='Operating day since Feb 1',ylabel='Cumulative actual cost (million CNY)')
            ax.grid(alpha=.25); ax.legend()
        (root/'reports/figures').mkdir(exist_ok=True)
        fig.savefig(root/'reports/figures/cumulative_cost.png',dpi=180)
        fig.savefig(root/'reports/figures/cumulative_cost.svg')
        plt.close(fig)
