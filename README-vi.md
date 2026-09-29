# ReleaseWitness

[English](README.md) | Tiếng Việt

[![CI](https://github.com/thuanlyt/releasewitness/actions/workflows/ci.yml/badge.svg)](https://github.com/thuanlyt/releasewitness/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/thuanlyt/releasewitness?display_name=tag&sort=semver)](https://github.com/thuanlyt/releasewitness/releases/latest)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Coding agent của bạn báo đã xong.
> ReleaseWitness kiểm tra bằng chứng đằng sau tuyên bố đó.
>
> Evidence và release assurance gắn với source cho code do AI viết.

> ReleaseWitness trước đây được phát hành với tên UseAgent trong series v0.1.x.

![Workflow release-assurance của ReleaseWitness cho dự án lập trình AI](docs-site/assets/relwit-control-plane-hero.webp)

## ReleaseWitness là gì?

ReleaseWitness giữ lại bằng chứng của quá trình lập trình có AI ngay trong
repository đang được thay đổi. Nó ghi provenance của evidence, giới hạn và
sanitize output bền vững, gắn QA với source snapshot, xác minh review evidence
và kiểm tra Git release durability trước quyết định release.

Kết quả là một trail cô đọng, có thể kiểm tra để trả lời các câu hỏi quan
trọng:

- Đã thay đổi gì, và source state nào đã được kiểm tra?
- Evidence là local, live, simulation hay blocked?
- Review và QA có xác minh đúng source chuẩn bị ship không?
- Repository đã sạch và đủ bền vững cho release gate tiếp theo chưa?

ReleaseWitness provider-neutral và trusted-local. Nó bổ sung cho coding
runtime, không cố trở thành coding runtime.

## Phiên bản hiện tại

**Phiên bản trong source hiện tại: v0.2.0 — Release Assurance, Rebranded.**
v0.2.0 là rename/identity migration từ UseAgent sang ReleaseWitness/RelWit,
không phải feature set mới; xem [CHANGELOG](CHANGELOG.md) để biết nội dung
chính xác. Để xem bản phát hành công khai mới nhất, xem
[GitHub Releases](https://github.com/thuanlyt/releasewitness/releases).
Feature set vẫn freeze; thay đổi tương lai cần bug cụ thể, vấn đề bảo mật,
evidence từ user bên ngoài hoặc quyết định rõ ràng của owner.

- [Tất cả bản phát hành](https://github.com/thuanlyt/releasewitness/releases) · [CHANGELOG](CHANGELOG.md)
- [Tài liệu](https://relwit.thuanlyt.id.vn/) · [Hướng dẫn bắt đầu](docs/getting-started.md)

Repository public không đóng gói runtime history của maintainer. `work/` được
`init` tạo local trong project đang được assurance.

## Vì sao dùng ReleaseWitness?

| Vấn đề release-assurance | Cách ReleaseWitness đáp ứng |
| --- | --- |
| Khó audit output của AI về sau | Evidence provenance có kiểu và handover có source anchor |
| Check xanh có thể thuộc về tree cũ | Source-bound QA và freshness check rõ ràng |
| Raw runner output có thể chứa secret | Summary bounded đã sanitize và diagnostic spool local |
| Nhầm “done” với “sẵn sàng release” | Review, QA và Git durability gate tách biệt |
| Công việc dài bị mất context bền vững | Knowledge card, report và checkpoint cô đọng |
| Runtime lập trình AI có workflow khác nhau | Một contract local chung quanh output của chúng |

## Nghiên cứu redundancy 2026-09: điều gì thay đổi

Một self-audit thực nghiệm có evidence trong repository đã chạy 56 session
Claude Code thật (Opus 5.5 làm lead, Sonnet 5.5 làm worker). Ba chế độ được so
sánh là Claude Code native, Claude Code native kèm RelWit assurance, và RelWit
supervision đầy đủ. Trong fixture này, supervision đầy đủ tốn hơn 1,9–4,8 lần
mỗi task và chậm hơn 1,8–5,6 lần nhưng không cải thiện correctness hay recovery
cuối cùng. Thuộc tính riêng còn lại của RelWit là verdict do tool tính để xác
nhận QA áp dụng cho đúng source đang release; thuộc tính đó cũng có thể tái tạo
bằng một Git hook nhỏ.

Đây không chỉ là lời tự báo cáo: repository chứa
[báo cáo đầy đủ](audit/redundancy-2026-09-30/RELWIT_REDUNDANCY_REPORT.md),
[experiment matrix](audit/redundancy-2026-09-30/EXPERIMENT_MATRIX.md),
[hướng dẫn reproduce](audit/redundancy-2026-09-30/REPRODUCE.md) và evidence đã
commit trong `audit/redundancy-2026-09-30/evidence/`.

Hệ quả:

- Để coding runtime lập kế hoạch, chọn model và thực thi. Chỉ dùng RelWit làm
  gate gắn với source: chạy `relwit qa`, rồi `relwit gate` trong CI hoặc agent
  hook.
- QA fingerprint giờ dựa trên nội dung source. Commit chỉ có evidence, hoặc
  sửa commit message, không còn làm QA mất hiệu lực.
- Lớp supervision (DAG, mailbox, runner, autopilot, telemetry) **deprecated**,
  chờ owner quyết định
  ([ADR-0011](knowledge/decisions/0011-assurance-only-boundary.md)).

## Nếu tôi đã dùng Claude Code, Codex, Beads hoặc worktree thì sao?

Các công cụ đó lập kế hoạch và thực thi công việc. ReleaseWitness xác minh
evidence và release state quanh repository sau khi công việc được thực hiện.
Nó đứng bên cạnh các công cụ đó, không bắt chúng từ bỏ planning, subagent,
branch hay worktree management vốn có:

```text
Claude Code / Codex / Beads / worktree
             lập kế hoạch và thực thi
                         ↓
ReleaseWitness xác minh evidence, source identity và release durability
```

ReleaseWitness là lớp bổ sung, không thay thế Beads, Spec Kit, native
Claude/Codex subagent, Git worktree manager hay CI của project.

## ReleaseWitness xác minh điều gì?

- Evidence có provenance được kiểm soát và source anchor có thể lặp lại.
- Runner/QA summary bền vững được giới hạn và sanitize; raw diagnostic mặc định
  chỉ nằm local.
- QA gắn với nội dung release source và QA config; Git HEAD và dirty state
  được ghi lại như provenance.
- `relwit gate` trả exit code khác 0 khi không có QA pass khớp với source hiện
  tại, nên CI hoặc coding-agent hook có thể chặn release.
- Cần review evidence trước khi work item được coi là done.
- Release durability kiểm tra Git cleanliness, untracked file và QA hiện tại
  tách biệt với task completion.
- Quality gate được cấu hình rõ; quyền deploy vẫn thuộc project owner.

## Supervisor nhẹ là capability tùy chọn (deprecated)

> Deprecated theo [ADR-0011](knowledge/decisions/0011-assurance-only-boundary.md).
> Trong nghiên cứu 2026-09, lớp này làm tăng chi phí và churn nhưng không cải
> thiện correctness so với orchestration native của Claude Code. Nó vẫn được giữ
> nguyên, không thay đổi, cho đến khi owner quyết định.

Khi project cần supervisor, `$relwit` có thể biến goal ngắn thành workflow hữu
hạn: ghi assumption, tạo work item theo dependency, dispatch assignment, ingest
report, kiểm tra evidence, chạy QA và checkpoint hành động tiếp theo. DAG,
mailbox, role và telemetry vẫn được giữ, nhưng đây là capability điều phối tùy
chọn — không phải identity chính của sản phẩm.

Role là persona của workflow, không phải identity của vendor:

| Role | Trách nhiệm |
| --- | --- |
| `supervisor` | Lập bounded work, review evidence và chọn hành động an toàn tiếp theo |
| `explorer` | Discovery chỉ đọc và source anchor |
| `planner` | Chia goal thành work item có scope |
| `worker` | Implement một scope đã claim và report check |
| `reviewer` | Xác minh diff, regression, security và evidence |
| `release_gate` | Kiểm tra release readiness và operational evidence |

Codex, Claude Code, Google Antigravity và coding agent khác có thể làm worker
khi đọc được contract của repository, chạy được CLI và tôn trọng scope. Xem
[hướng dẫn runtime thực tế](docs/getting-started.md).

## Giám sát có judgment

Supervisor tùy chọn ghi lại ranh giới quyết định, không ghi chain-of-thought
ẩn. Mỗi bounded cycle nên nêu intent, tradeoff, owner, evidence anchor và
next action hoặc điều kiện dừng. Khi marginal value thấp hoặc evidence còn mơ
hồ, supervisor nên dừng và hỏi owner thay vì tạo thêm việc. Cách này giữ điều
phối hữu ích, tôn trọng diminishing returns và ranh giới trusted-local.

## Quick start cho project bên ngoài

Thông thường ReleaseWitness được clone hoặc cài một lần, sau đó trỏ vào
repository muốn kiểm tra. Không dùng checkout source của ReleaseWitness làm
application workspace mặc định.

Yêu cầu: Python 3.11+, Git và một target repository đã tồn tại.

```powershell
git clone https://github.com/thuanlyt/releasewitness.git F:\tools\RelWit
python F:\tools\RelWit\relwit\cli.py --root F:\dev\MyProject init
```

`init` tạo local state rỗng tại `F:\dev\MyProject\work`. Nó không copy skill
hoặc ghi đè file project. Để dùng đầy đủ workflow, hãy copy hoặc merge các
file control-plane của ReleaseWitness (`AGENTS.md`, `.agents/skills/`,
`knowledge/`, `relwit/` và config) vào target repository, giữ nguyên
instruction và source của target. Sau đó chạy:

```powershell
python F:\tools\RelWit\relwit\cli.py --root F:\dev\MyProject validate
```

Boundary `--root` bao phủ registry, report, evidence, checkpoint và mọi path
đã cấu hình. Path đi ra ngoài boundary sẽ bị từ chối. Nếu đã cài CLI, dùng:

```powershell
python -m pip install --no-deps F:\tools\RelWit
relwit --root F:\dev\MyProject init
relwit --root F:\dev\MyProject validate
```

`python -m relwit` tương đương entry point `relwit` đã cài.

Đọc [hướng dẫn bắt đầu](docs/getting-started.md) trước khi đăng ký worker.

## Vòng assurance

Core path dùng được với một agent hoặc nhiều agent:

```text
implement → report evidence → review → source-bound QA → Git durability gate
```

Dùng chỉ cho assurance thì không cần work item, roster hay supervision skill:

```bash
relwit qa                      # chạy QA đã cấu hình, gắn kết quả với source hiện tại
relwit gate --require-clean    # exit 0 chỉ khi QA đó còn áp dụng cho commit sạch
```

Trong Claude Code, một `PreToolUse` hook bảo vệ lệnh release có thể chạy
`relwit gate --require-clean >&2 || exit 2`; exit code 2 sẽ chặn tool call.

Nếu bật supervision (deprecated), vòng tùy chọn thêm:

```powershell
relwit supervisor cycle --run-qa
relwit supervisor report --check
```

Worker có thể dùng mailbox/report protocol được tạo sẵn, nhưng project cũng có
thể đặt ReleaseWitness quanh công việc do orchestrator bên ngoài lập kế hoạch.
Worker report không phải release decision; review, QA và durability gate vẫn
tách biệt.

## Trust model và ranh giới concurrency

ReleaseWitness được thiết kế cho threat model **trusted-local /
trusted-repository**. Scope và role check là workflow control, không phải OS
sandbox, distributed lock được authenticate hay hệ thống authenticated agent
identity.

ReleaseWitness không sở hữu:

- branch, worktree hay process isolation song song;
- provider account, quota hay vendor API launch flag;
- task graph nếu một orchestrator khác đang sở hữu;
- deploy hoặc external mutation.

External orchestrator có thể quản lý branch, worktree, parallel execution và
task graph. ReleaseWitness xác minh repository state sau đó. Shared-folder và
mailbox workflow trong guide supervision là cố ý nhẹ và trusted-local.

## Bản đồ repository và tài liệu

| Path | Mục đích |
| --- | --- |
| `.agents/skills/` | Skill supervisor, context, worker, review và autopilot tùy chọn |
| `.codex/agents/` | Profile Codex theo role, tùy chọn |
| `knowledge/` | Project brief, architecture, contract và decision cô đọng |
| `relwit/` | Assurance CLI và validator, không dependency (`relwit/cli.py`) |
| `relwit.config.json` | Cấu hình path, QA và production-readiness |
| `work/` | Registry, report, evidence và checkpoint local được tạo sau `init` |
| `docs/` | Tài liệu thao tác, vận hành, architecture và case study chuẩn |
| `docs-site/` | Website tài liệu tĩnh song ngữ, crawlable |
| `tests/` | Regression test standard library và docs-site |

[Case study dogfood OSBlog](docs/case-study-osblog.md) cho thấy workload thật
đã dùng evidence, QA và recovery boundary ra sao. OSBlog là workload và nguồn
evidence, không phải sản phẩm được định vị ở đây. Case study này có trước đợt
rebrand nên vẫn trích dẫn ID work-item `UA-####` gốc mà nó được evidence dưới
tên đó.

## Ghi chú đóng gói

Public entry point hiện là `relwit = relwit.cli:main`, dùng package top-level
riêng `relwit` (`packages = ["relwit"]`). Cách này thay thế layout namespace
`tools` cũ (`useagent = tools.useagent:main`), vốn đã được gỡ bỏ trong đợt
rebrand ReleaseWitness để loại rủi ro collision của một top-level package tên
chung. CLI đã cài và wheel smoke path vẫn không dependency.

## Đóng góp và giấy phép

Đóng góp tuân theo [work-item và review contract](CONTRIBUTING.md). Khi báo cáo
vấn đề bảo mật, hãy đọc [SECURITY.md](SECURITY.md). Dự án phát hành theo [MIT
License](LICENSE).

---

## 💖 Support the Project

ReleaseWitness là **miễn phí và mã nguồn mở**. Nếu dự án giúp bạn tiết kiệm thời gian, hãy tặng một ⭐ **Star** — đó là động lực để dự án tiếp tục phát triển và có thêm nhiều skill hơn.

<a href="https://github.com/thuanlyt/releasewitness/stargazers">
  <img src="https://img.shields.io/github/stars/thuanlyt/releasewitness?style=social" alt="GitHub Stars">
</a>

### 🤝 Cộng đồng & Hỗ trợ
- 📖 [Đọc tài liệu](https://relwit.thuanlyt.id.vn/)
- 🐛 [Báo lỗi](https://github.com/thuanlyt/releasewitness/issues)
- 🌐 [Website ThuanLYT](https://thuanlyt.id.vn)

<p align="center"><em>Được xây dựng bằng ❤️ bởi ThuanLYT</em></p>
