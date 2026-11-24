# 社区修理咖啡馆零件转供采录台

用合成的夹板铰链和电池灯外壳卡片演示社区志愿维修中的捐赠件保留、台位预约、跨单预留、可逆装配与归还。所有运行在本地 SQLite 中完成，操作只处理台账。需要 Python 3.11+ 和 SQLite 3.37+（用于严格表类型）。在本目录运行，无需安装依赖。

```powershell
python -B -m repair_cafe --help
python -B -m repair_cafe --db cafe.sqlite demo
python -B -m repair_cafe --db cafe.sqlite plan
python -B -m repair_cafe --db cafe.sqlite audit
python -B -m unittest discover -s tests -v
```

`load` 接受 UTF-8 JSON 批次，例如 `python -B -m repair_cafe --db cafe.sqlite load fixtures/rejected.json`。在演示库中，这份夹具先提交高版本不合格证据，再尝试装配同一件，整批失败退出并回滚。如果需要保留新检查，应将它单独作为一批采入。`demo` 可在同一库重跑。`plan` 和 `audit` 只读，审计按采入顺序重放所有成功事件并逐表比较业务状态，发现差异时退出失败。

| 事件 | `id`、动作名之后的参数 |
|---|---|
| `donate` | 件号、规格、供体标签、`keep` 或 `release` |
| `inspect` | 件号、正整数版本、合格 `1` 或不合格 `0` |
| `retention` | 件号、`keep` 或 `release` |
| `substitute` | 来件规格、申请规格、额外装配分钟 |
| `request` | 申请号、需求规格、技能、基础分钟、优先级 |
| `volunteer` | 志愿者号、技能 |
| `book` | 申请号、台位、志愿者、开始分钟、结束分钟 |
| `cancel` | 申请号 |
| `allocate` | 无参数，按当前候选集提交整批预留 |
| `fit` / `release` | 申请号。归还前可撤销装配或取消预留 |
| `check` | 申请号、独立同技能复核者、通过 `1` 或失败 `0` |
| `handover` | 申请号 |

- `parts.py` 处理供体保留许可、最高版本检验和单向替代规则。晚到旧版本留档，当前资格按最高版本计算。`donations` 输出证据和许可状态，库存空闲还须结合 `allocations` 查看。
- `bench.py` 分别阻止台位和志愿者的时间冲突，采用半开分钟区间。取消已占件的预约前须撤销预留或装配。
- `repairs.py` 对这一场次的小批量候选执行增广匹配，保留既有预留。先尝试高优先级申请，同级按编号，候选按所需补时和件号排序。必要时重排本轮尚未提交的候选，争取更多可执行申请。演示中 `flex` 使用替代卡片，只有 15 分钟的 `strict` 得到原规格铰链。装配账保存原工人、实物及当时采用的检验版本，撤装后也可复核。交还要求独立通过凭证针对当前装配和当前最高合格检验版本；后到的更高检验使旧通过暂时失效。最终归还后锁定该装配和实物。
- `store.py` 保存业务账。`repair_cafe.py` 串行提交批次，完整事件重复成功跳过。同 ID 异内容、业务失败或约束失败时回滚整批。

## 取件柜台

台账中的交还事件仍记录已完成的维修。前台给已完成的维修挂一个柜台牌，再采录持牌人的纸质存根。柜台交接与维修投影分别记账，正常审计重放两者。

```sh
python -B -m repair_cafe --db repair.sqlite demo
python -B -m repair_cafe --db repair.sqlite load fixtures/counter-pickup.json
python -B -m repair_cafe --db repair.sqlite counter
```
