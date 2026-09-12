# C题：附件与结果模板的CSV整理说明

## 1\. 文件与适用范围

本包包含两个CSV，均为UTF-8 with BOM、逗号分隔、有表头的长表：

* `attachments\_clean.csv`：附件1—4的原始数据，共 **193,152条数值记录**、25列。
* `results\_template\_clean.csv`：五份result文件的统一待填模板，共 **360,880条待填记录**、25列。

“一条记录”是一个带类型的数值/待填指标，不等于一个独立时间点。
例如，同一十分钟的负载和电价各占一行；同一目标时刻的不同预报版本也各占一行。
不能不分类型地对整份CSV求和、拼接成单变量序列或进行缺失值填充。

原始9个XLSX以及`题c.md`均未改写。此次工作只整理用户确认的六项数据/模板语义，
不运行预测、储能优化、费用结算或策略评价。
CSV是项目内部数据与填写格式，不改变题目要求最后提交对应XLSX文件的要求；
本次没有生成已求解的提交答案。

## 2\. 六项处理规则

### 2.1 `00:10`是00:00—00:10区间的右端点标签

按用户确认，将附件1、2、4的144个时间标签映射到当天144个十分钟区间：

|原标签|slot\_index|start\_minute|end\_minute|规范区间|
|-|-:|-:|-:|-|
|00:10|0|0|10|00:00—00:10|
|00:20|1|10|20|00:10—00:20|
|23:50|142|1420|1430|23:40—23:50|
|0:00+1|143|1430|1440|23:50—次日00:00|

调度区间编码采用左闭右开`\[start,end)`，避免边界重复归属。
`end\_minute=1440`明确表示该调度日的24:00，不是该日开头的00:00。
有真实日期时，同时写入完整起止日期时间。例如2025-12-31最后一区间的
`interval\_end`为`2026-01-01T00:00:00`，但`day\_id`仍是`2025-12-31`。

附件1没有给出具体日历日期，因此使用`day\_id=typical\_day`、
`time\_basis=relative\_day`以及分钟偏移，不把它擅自归到2025-01-01。
其`interval\_start`、`interval\_end`留空是“不适用”，不是时间丢失。
所有日历时间均按题目原时间表达，**不根据操作者所在地转换时区，也不擅自添加UTC偏移**。

### 2.2 单位显式化，原数值不改动

输入指标名明确含有`\_kw`或`\_cny\_per\_kwh`，并单独保留`unit`列。
输出能量指标使用`\_kwh`，费用指标使用`\_cny`。`CNY`就是人民币元。

十分钟区间功率换算电量时，按本次采用的区间代表功率口径：
`energy\_kwh = power\_kw \* 10 / 60`。
本次CSV仍保留原功率数值，**没有将所有kW数值预先除以6**，也没有添加派生能量记录。
附件3是整点预报，不应把每个预报点直接除以6后当成十分钟电量。

### 2.3 附件3分离发布时刻、目标时刻与预报提前量

每个预报数值独立成行，填写：

* `issue\_time`：发布时刻。
* `valid\_time`：预报目标时刻。
* `lead\_hours`：1至24，满足`valid\_time = issue\_time + lead\_hours小时`。

`time\_semantics=forecast\_point`。其十分钟区间字段全部留空。
不把整点预报假装成已经给出了十分钟平均功率，也不在整理时选择插值算法。
2025-12-31发布、目标落到2026年的 **40条** 预报全部保留，最大目标时刻为
`2026-01-01T18:00:00`。

### 2.4 不按目标时刻去重

附件3的唯一业务键为：
`(dataset\_id, issue\_time, valid\_time)`，
不是`valid\_time`单列。全部 **1,460次发布 × 24个预报 = 35,040条** 保留。

同一目标`2025-01-01T07:00:00`在原始数据中至少有这两版：

|issue\_time|valid\_time|lead\_hours|value|unit|
|-|-|-:|-:|-|
|2025-01-01T00:00:00|2025-01-01T07:00:00|7|3.1173|kW|
|2025-01-01T06:00:00|2025-01-01T07:00:00|1|3.0919|kW|

它们不是重复或冲突记录，不能取平均后覆盖。
在某个决策时刻挑选预报时，先限制`issue\_time <= decision\_time`，
再按模型规则选择可用版本。不能先全局保留“最新预报”再回头制定零点计划。

附件1的光伏预测是另外一个数据集`A1\_PV\_FORECAST`；
原表没有逐条发布时间，故其`issue\_time`保持为空，不伪造元数据。

### 2.5 附件3的空日期只继承同一日期组

原表365行写有完整日期，其余 **1,095行** 在日期列为空。
仅对这些日期空白进行组内向下继承，并核验每一天的发布时间依次为0、6、12、18点。
展开为长表后，有 **26,280条** 预报记录的`date\_inherited=1`。

`raw\_date`保留原行的空白；`source\_date\_cell`指向提供日期的原始锚点单元格。
这不等于填充预报数值。所有附件的数值均无缺失；其中 **41,120个真实零值** 全部保留。

### 2.6 结果模板按记录类型重新组织

全部原始result数值单元格均为空，因此依据题目时间范围重建待填结构，
不对任何已计算答案进行移位，也不宣称旧标签的官方勘误方式已经得到确认。

* 购电时间轴统一为00:00—00:10至23:50—24:00，共144段。
* 问题2、3、4的日期完整展开为2025-02-01至2025-12-31，共334天，删除省略号占位。
* 四小时充/放电区间和0:00、24:00储电状态拆为不同记录，绝不再按Excel横向排版关联。
* 问题3与4-3为6、12、18点修订分别留出版本。只对当日尚未开始的区间预留修订行：
6点108段、12点72段、18点36段；过去区间仍由既有计划记录。
* 每天只预放一个紧急事件的空白填写位置，实际有几次就填写/追加几次，没有1次或3次上限。
* 所有`value`为空、`status=unfilled`。空白不是0，也不是已经发生或已经完成的结果。

问题1原XLSX没有“全天购电量/购电费”位置，依照`题c.md`问题1表1补充两条待填汇总记录。
其`source\_file`明确指向`题c.md`，没有伪装成原XLSX中已有的单元格。

## 3\. 输入CSV的数据集与字段

### 3.1 数据集

|dataset\_id|原始文件 / 工作表|metric|unit|行数|含义|
|-|-|-|-|-:|-|
|`A1\_PRICE`|附件1.xlsx / Sheet1|`electricity\_price\_cny\_per\_kwh`|CNY/kWh|144|未指定日期的典型日电价|
|`A1\_LOAD`|附件1.xlsx / Sheet1|`load\_kw`|kW|144|典型日负载|
|`A1\_PV\_FORECAST`|附件1.xlsx / Sheet1|`pv\_forecast\_kw`|kW|144|典型日光伏预测|
|`A2\_LOAD`|附件2.xlsx / 小区负载|`load\_kw`|kW|52,560|全年实际负载|
|`A2\_PV\_ACTUAL`|附件2.xlsx / 光伏发电实际功率|`pv\_actual\_kw`|kW|52,560|全年实际光伏|
|`A3\_PV\_FORECAST`|附件3.xlsx / Sheet1|`pv\_forecast\_kw`|kW|35,040|全年1460次发布、每次24个整点预报|
|`A4\_PRICE`|附件4.xlsx / Sheet1|`electricity\_price\_cny\_per\_kwh`|CNY/kWh|52,560|全年分时电价|

`A1\_PRICE`只保留原始144个价格值，没有为了问题2、3复制365份。
`A4\_PRICE`与之分开存放。数据集适用于哪个问题，以`题c.md`为准，不能因为数值可读取就默认决策时已知。

### 3.2 全部字段

|字段|含义|
|-|-|
|`record\_id`|本CSV内唯一记录编号；仅用于追踪，不是模型特征。|
|`dataset\_id`|逻辑数据集标识；必须据此区分附件、实际值、预报与电价。|
|`source\_file`|原始附件文件名。|
|`source\_sheet`|原始工作表名。|
|`source\_cell`|该数值在原工作表中的单元格地址。|
|`source\_date\_cell`|日期来源单元格；继承日期时指向上方提供完整日期的锚点；附件1为空。|
|`source\_time\_cell`|时间标签或预报发布时刻在原表中的单元格地址。|
|`raw\_date`|该数值所在原始行的日期文本/Excel序列值；原日期为空则仍为空，只用于追溯。|
|`raw\_time`|原时间标签的底层文本/Excel小数；仅用于追溯，不作为标准时间。|
|`date\_inherited`|0或1；1表示附件3该源行的空日期继承了同一日期组的完整日期。|
|`time\_basis`|calendar=有真实日期；relative\_day=题目未指定日期的典型日。|
|`day\_id`|calendar时为YYYY-MM-DD；附件3为发布日，不是目标日；relative\_day时为typical\_day。|
|`slot\_index`|十分钟区间编号，0至143；附件3整点预报为空。|
|`start\_minute`|相对day\_id当天0:00的区间起点分钟数；附件3为空。|
|`end\_minute`|相对day\_id当天0:00的区间终点分钟数；1440明确表示日末；附件3为空。|
|`interval\_start`|有真实日期的区间起点，YYYY-MM-DDTHH:MM:SS；典型日和整点预报为空。|
|`interval\_end`|有真实日期的区间终点；最后一个区间为次日00:00；典型日和整点预报为空。|
|`issue\_time`|仅附件3填写的预报发布时刻；不能替换为目标时刻。|
|`valid\_time`|仅附件3填写的预报目标时刻，可跨日、跨年。|
|`lead\_hours`|附件3预报提前小时数，1至24；valid\_time=issue\_time+lead\_hours小时。|
|`time\_semantics`|ten\_minute\_interval=按右端点标签归属的十分钟区间；forecast\_point=整点功率预报。|
|`metric`|指标名，含物理量与单位后缀；实际光伏与预测光伏分别命名。|
|`value`|单个原始数值；直接保留原XLSX的数值文本，不做四舍五入、不做预测或优化。|
|`unit`|kW或CNY/kWh；CNY代表人民币元。|
|`value\_status`|present=有原始数值，missing=源数值缺失；本次全部为present。|

时间区间数据的唯一业务键：
`(dataset\_id, day\_id, slot\_index)`。

原始数值追溯键：
`(source\_file, source\_sheet, source\_cell)`。
本次每个原数值单元格恰好输出一次，`value`与XLSX底层保存的数值文本逐项相同，
未四舍五入，也未删去零值。

`raw\_date`可能是Excel日期序列值；`raw\_time`可能是Excel一天的小数比例。
**它们不是供模型直接使用的时间列。** 应使用`day\_id`、分钟字段和完整时间字段。

## 4\. 输出CSV的记录类型与填写规则

### 4.1 记录类型

|record\_type|metric|版本/含义|
|-|-|-|
|planned\_purchase|planned\_purchase\_kwh|`initial\_00`：零点制定的每十分钟购电量|
|planned\_daily\_summary|daily\_planned\_purchase\_kwh / daily\_planned\_purchase\_cost\_cny|零点计划的全天汇总|
|adjusted\_purchase|revised\_plan\_purchase\_kwh|`update\_06/12/18`：该次修订后的时段计划总量，不是增减差额|
|adjusted\_daily\_summary|daily\_adjusted\_purchase\_kwh / daily\_adjusted\_purchase\_cost\_cny|原调整表的全天汇总位置，保留而不代定费用口径|
|storage\_flow|charge\_kwh / discharge\_kwh|`final`：六个四小时区间分别累计充、放电量|
|storage\_state|stored\_energy\_kwh|`final`：0:00与24:00的储电量状态|
|emergency\_event|emergency\_purchase\_kwh|`final`：实际紧急购电事件的区间总电量；初始仅为空白位置|

### 4.2 全部字段

|字段|含义|
|-|-|
|`record\_id`|本CSV内唯一记录编号；追加紧急事件时必须生成新编号。|
|`result\_id`|result1、result2、result3、result4-2、result4-3；区分五项结果。|
|`record\_type`|结果记录类别；必须先筛选类别再解释其余字段。|
|`time\_basis`|calendar或relative\_day；与附件CSV相同。|
|`day\_id`|该结果所属的调度日；问题1为typical\_day，其余为YYYY-MM-DD。|
|`slot\_index`|十分钟购电区间编号0至143；非十分钟购电记录为空。|
|`decision\_minute`|相对调度日0:00的计划制定时刻；初始计划0，日内修订360/720/1080；其他为空。|
|`decision\_time`|有真实日期的计划制定时刻；问题1使用decision\_minute=0而不虚构日期。|
|`plan\_version`|initial\_00、update\_06、update\_12、update\_18、final或day\_summary。|
|`start\_minute`|区间起点分钟数；紧急事件在计算前留空，储能状态行不使用该字段。|
|`end\_minute`|区间终点分钟数；1440表示日末。|
|`interval\_start`|有真实日期的区间起点；相对日期或尚未填写的事件为空。|
|`interval\_end`|有真实日期的区间终点；日末使用次日00:00。|
|`state\_minute`|仅储电量状态使用，0或1440；不与充放电区间共用一行。|
|`state\_time`|有真实日期的储电量状态时刻。|
|`event\_index`|紧急购电事件序号；初始1仅表示一个待填位置，不代表发生过事件或事件数上限。|
|`metric`|结果指标，能量使用\_kwh，费用使用\_cny。|
|`value`|待计算/填写的数值；本文件全部为空，绝不能将空白自动解释为0。|
|`unit`|kWh或CNY；CNY代表人民币元。|
|`status`|初始为unfilled；完成时filled；未使用的可选修订可记not\_used；无紧急事件可记no\_event。|
|`value\_definition`|该数值是区间总量、时点状态、调整后计划总量或尚须明确口径的汇总量。|
|`source\_file`|结构依据的原文件名；问题1新增的全天汇总来自题c.md。|
|`source\_sheet`|结构依据的原工作表名；题目正文来源为空。|
|`source\_reference`|旧模板空白数值单元格或题目位置；仅作结构追溯，不能把旧时间标签套回新CSV。|
|`row\_origin`|记录是重建时间轴、拆分并排表、展开省略日期、扩展修订版本或保留汇总的哪一种。|

`source\_reference`对原模板空格的引用只用于追溯。这些旧空格原本配有有歧义的时间标签，
CSV已经重建规范时间轴。**不能按旧标签解释新CSV，也不能不做映射就直接回填原XLSX。**
对原模板没有明列的日期和版本，`row\_origin`明确记录扩展原因。

### 4.3 修订版本不得相加

每条`revised\_plan\_purchase\_kwh`明确表示**修订后的计划总量**。
例如原计划100、修订后80，则此处填80，不是-20。
这是本CSV的字段定义，不自动决定违约费用或退款规则。

同一供电时段的初始计划、6点版、12点版、18点版是同一计划的不同版本，
不能把它们当成多笔购电而相加。
重建某时刻的完整计划时，从初始计划出发，用当时已经生效、且被模型采用的最新修订覆盖相应未来区间。
最终执行量和全天费用的计算仍由你们的模型负责。

不使用某次预报修订时，可将对应行`status`记为`not\_used`、`value`留空。
使用某次预报但某时段购电量恰好不变时，应填写与上一有效版本相同的计划总量，
而不是用0表示“无变化”。

原调整表“全天购电量/全天购电费”的结算范围不在本次六项确认之中，
所以`adjusted\_daily\_summary`的`value\_definition`标记为
`summary\_scope\_requires\_model\_definition`。填写前须与模型中的汇总口径一致，
尤其不得对多个完整计划版本直接求和。其他效率、计量侧、退款、价格可知性问题也未在重排中擅自裁定。

### 4.4 充放电与状态

每个四小时区间的充电量、放电量分开累计，不能用储电量的净变化替代两个累计量。
计量侧和效率公式沿用你们另行明确的模型定义，本次未将任一效率假设写入数值。

`storage\_state`行只用`state\_minute/state\_time`；0点和24点分别为独立记录。
例如2025-02-01的日末状态，`day\_id=2025-02-01`、
`state\_time=2025-02-02T00:00:00`。它不能因与某个充放电区间在原表中同行而被关联到该区间。

### 4.5 紧急事件的状态

初始`event\_index=1`只是一个待填位置，不代表当天已经有一次事件。
完成某一天模拟之后：

* 有事件：填写起止分钟、起止日期时间、区间总电量，设`status=filled`。
有多个事件时复制该日事件记录，递增`event\_index`并赋予唯一`record\_id`。
* 无事件：保留该日一条`status=no\_event`记录，区间和`value`保持空白。
* 尚未模拟：仍为`status=unfilled`，不能写成`no\_event`或自动补0。

因此，`value`为空必须结合`status`理解；`no\_event`与`unfilled`含义完全不同。
事件时长由实际结果决定，不受模板原有空行数限制。

### 4.6 各结果的待填行数

|result\_id|行数|
|-|-:|
|`result1`|160|
|`result2`|53,774|
|`result3`|126,586|
|`result4-2`|53,774|
|`result4-3`|126,586|
|**合计**|**360,880**|

较多行数来自长表、完整日期展开、独立充放电/状态记录以及日内修订版本，
不是额外观测数据，也不是计算得到的购电策略。

## 5\. 给AI Agents的读取约束

1. 训练和输入只从附件CSV中选择适用的数据集；绝不把result模板当训练数据。
2. 先检查`dataset\_id/record\_type/time\_semantics/time\_basis`再解析字段；不对整表做统一`dropna`或前向填充。
3. 十分钟区间按`day\_id + slot\_index`对齐；`00:10`不再解释为00:10—00:20。
4. 附件3按`issue\_time + valid\_time`保留版本；使用前按决策时刻过滤发布版本。
5. `kW`和`kWh`不得混用；`value=0`是真实零，空值必须结合状态列解释。
6. 不虚构附件1日期，不截掉跨日或跨年预报，不因CSV行顺序推定因果时间顺序。
7. 日内修订是总计划版本，不是额外采购，也不是差额；没有使用的版本必须显式标记。
8. 本次未给出插值、费用结算、效率、储能初末条件的新增答案；不能把整理约定误写成题目已明确规定的算法条件。

Python标准库读取示例：

```python
import csv
from decimal import Decimal

with open("attachments\_clean.csv", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        if row\["dataset\_id"] == "A2\_LOAD":
            load\_kw = Decimal(row\["value"])
            start\_minute = int(row\["start\_minute"])
            end\_minute = int(row\["end\_minute"])
            energy\_kwh = load\_kw \* Decimal(end\_minute - start\_minute) / Decimal(60)
            # 用day\_id与slot\_index对齐其他十分钟区间数据。
            # 不要在这里把预报点与实际值无条件拼成同一条时间序列。
```

## 

