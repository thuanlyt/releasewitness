# ReleaseWitness hands-on onboarding

This is the practical guide for a first-time user. ReleaseWitness is primarily a
repo-local evidence and release-assurance layer; its lightweight supervisor
workflow is optional. It answers four questions:

1. Which agents can participate?
2. What must be registered in `relwit.config.json`?
3. What does the user actually type in Codex, Claude Code or Antigravity?
4. Which Markdown files prove that work was assigned, reported and verified?

> **Start here if you already use Claude Code, Codex or another coding agent.**
> The 2026-09 [redundancy study](../audit/redundancy-2026-09-30/RELWIT_REDUNDANCY_REPORT.md)
> found that native orchestration matched RelWit supervision at lower cost. The
> recommended use is assurance-only: configure `supervisor.qa_commands`, run
> `relwit qa`, and use `relwit gate --require-clean` as a CI step or agent hook.
> The supervision walkthrough below is deprecated
> ([ADR-0011](../knowledge/decisions/0011-assurance-only-boundary.md)).

> **Vietnamese / Tiếng Việt:** Nếu đây là lần đầu bạn dùng ReleaseWitness, hãy làm đúng
> ví dụ từ đầu đến cuối. Bạn không cần tự thiết kế DAG hoặc viết prompt task dài;
> supervisor sẽ tạo assignment trong `work/outbox/`.

## 1. The mental model

ReleaseWitness has two kinds of identity:

- **Role**: what an agent does in the workflow: supervisor, explorer, planner,
  worker, reviewer or release gate.
- **Runtime identity**: the name of one real running session, for example
  `codex-api`, `claude-web` or `antigravity-qa`.

The vendor is not the identity. A Codex session and a Claude Code session are
both just workers if they are assigned implementation work. The supervisor uses
the runtime identity to route a task to the right mailbox.

![Codex, Claude Code and Antigravity sharing one assignment and report protocol](../docs-site/assets/relwit-runtime-handoff.svg)

*Different runtimes can join the same repository because the assignment,
scope and report contract stays provider-neutral.*

```text
User goal
   |
   v
Supervisor model (any capable runtime)
   | creates task + assignment + outbox prompt
   v
codex-api       claude-web       antigravity-qa
   |                 |                  |
   +------ report Markdown + evidence -+
                         |
                         v
                 supervisor cycle + QA
```

### Role catalogue

| Role | Responsibility | Automatically dispatched? |
| --- | --- | --- |
| `supervisor` | Understand the goal, plan the DAG, dispatch, ingest reports, run QA and choose the next bounded action. | No. It is the brain session. |
| `explorer` | Read-only repository mapping and source-anchored context. | No. Spawn or open when needed. |
| `planner` | Break a goal into dependency-aware work items. | No. Usually used by the supervisor. |
| `worker` | Implement exactly one claimed work item and report evidence. | Yes, when registered and eligible. |
| `reviewer` | Review diff, regressions, security and test gaps. | No. Review is a gate, not an implementation worker. |
| `release_gate` | Check release evidence, operational notes and remaining risk. | No. Run at a milestone boundary. |

The repository includes optional Codex profiles for these roles in
`.codex/agents/`. The default configuration registers only `supervisor`; you
register the worker sessions that really exist in your project.

### Runtime compatibility

ReleaseWitness is file-first and provider-neutral. A runtime is compatible when it can
open the same repository, read Markdown, run the CLI and write code only inside
its assigned scope.

| Runtime | How it fits | What the user must do |
| --- | --- | --- |
| Codex | Tightest integration. It can use `$relwit` / `$relwit-worker` and the optional `.codex/agents` profiles. | Open the repository as the workspace, give the worker its registered id, and let it pull/report through the CLI. |
| Claude Code | Compatible through the same `AGENTS.md`, skills and Markdown protocol. ReleaseWitness does not call the Anthropic API. | Start `claude` in the same control-plane root; explicitly tell it to read the ReleaseWitness worker skill and pull its id. |
| Google Antigravity | Compatible through a local Project, shared files and the repository `.agents/skills` directory. | Add the repository as a Project, choose local/shared-folder mode for the simple setup, then give the agent the same worker prompt. |
| Any other coding agent | Compatible if it has repository file access and a shell or equivalent way to run `relwit/cli.py`. | Use the generated `work/outbox/*-to-<agent-id>.md` prompt and preserve the report contract. |

Claude.ai chat without local file and shell access is not a worker runtime. An
Antigravity or other UI that cannot access the repository is also not enough;
the agent must be attached to the Project containing the control-plane files.

Official runtime references: [Claude Code setup](https://docs.anthropic.com/en/docs/claude-code/getting-started),
[Claude Code project memory](https://docs.anthropic.com/en/docs/claude-code/memory),
[Antigravity Projects and agents](https://antigravity.google/docs/home), and
[Antigravity skills](https://antigravity.google/docs/sdk/tools/).

## 2. Put the control plane around the target repository

The canonical setup keeps the ReleaseWitness source checkout or installed CLI
separate from the application repository. `--root` points the generated
evidence and optional coordination state at the target project:

```text
F:\dev\DemoStore\
├── AGENTS.md
├── .agents\skills\
├── knowledge\
├── relwit\cli.py
├── relwit.config.json
└── work\
```

Clone or install ReleaseWitness once. For an existing project, copy or merge the
control-plane files from this repository into the application repository when
you need the full optional workflow. Preserve the application's existing
`AGENTS.md`, `knowledge/`, generated `work/` and `relwit.config.json` content;
merge instructions and configuration instead of blindly overwriting them.
`work/` is local runtime state and is intentionally not inherited from this
public repository.

Then run these commands from the target repository root:

```powershell
relwit init
relwit validate
```

If the ReleaseWitness CLI lives in a central checkout, pass the target repository
explicitly. The target directory must already exist; `init` creates empty
runtime state there and the CLI rebinds the registry, config, lock and every
configured Markdown path to that root:

```powershell
python F:\dev\RelWit\relwit\cli.py --root F:\dev\DemoStore init
python F:\dev\RelWit\relwit\cli.py --root F:\dev\DemoStore validate
```

`--root` is accepted before the subcommand. Relative configured paths are
resolved under the selected root and are rejected if they escape it. This is
useful for a central CLI, but the target project still needs the ReleaseWitness
control-plane files before `validate` can pass.

If `validate` fails, fix the layout before registering workers. Do not start
coding against a half-initialized registry.

You can also install the CLI from a ReleaseWitness checkout and invoke it from the
target project:

```powershell
python -m pip install --no-deps .
relwit validate
```

The installed command uses the current directory as its default root. For a
central CLI, use `relwit --root F:\dev\DemoStore validate`.

> **Tiếng Việt:** ReleaseWitness hiện là control plane nằm trong repository. Vì vậy
> hãy đặt `relwit/cli.py`, `.agents/skills/`, `knowledge/`, `work/`,
> `AGENTS.md` và `relwit.config.json` trong thư mục gốc của dự án cần làm.
> Nếu dự án đã có các file này, hãy merge nội dung; không ghi đè mù.
> Nếu CLI nằm ở checkout trung tâm, dùng `python F:\dev\RelWit\relwit\cli.py
> --root F:\dev\DemoStore init`. Root phải tồn tại và mọi path cấu hình phải nằm
> bên trong root đã chọn.
> Có thể cài lệnh `relwit` bằng `python -m pip install --no-deps .`, sau đó
> chạy `relwit validate` tại root dự án.

## 3. Complete example: Codex + Claude Code + Antigravity

The following example assumes:

- target repository: `F:\dev\DemoStore`;
- Codex is the supervisor;
- Codex implements the backend;
- Claude Code implements the web client;
- Antigravity implements a QA/documentation slice;
- no worker is allowed to own another worker's source subtree.

### Step A — register the real worker sessions

Run these commands once from `F:\dev\DemoStore`:

```powershell
relwit agent register `
  --id codex-api `
  --role worker `
  --scope src/backend `
  --scope tests/backend `
  --capability python `
  --max-active 1

relwit agent register `
  --id claude-web `
  --role worker `
  --scope src/frontend `
  --scope tests/frontend `
  --capability web `
  --max-active 1

relwit agent register `
  --id antigravity-qa `
  --role worker `
  --scope tests/qa `
  --scope docs/qa `
  --capability browser `
  --max-active 1
```

Check the roster:

```powershell
relwit agent list
```

The names are arbitrary, but they must be unique and must match the session
that will run `worker pull --agent <id>`. Do not register `claude` once and
expect three Claude sessions to share that identity; give each concurrent
worker its own id.

### Step B — give the supervisor one light goal

Open the supervisor runtime in the target repository and send only this:

```text
Use $relwit in F:\dev\DemoStore.

Goal: build a production-ready DemoStore web application with a Python backend,
a web frontend, automated tests and operational documentation.

Available worker sessions: codex-api (Python backend), claude-web (frontend),
antigravity-qa (browser QA and docs).

Use the existing repository conventions. Create a dependency-aware roadmap and
scoped tasks, dispatch ready work, inspect Markdown reports, run QA, create debug
tasks when evidence fails, and continue one bounded cycle at a time. Do not
deploy, change secrets or delete data without my explicit approval.
```

The supervisor should create work items and then run:

```powershell
relwit supervisor cycle --run-qa
```

Inspect what was generated:

```powershell
Get-ChildItem work\outbox
Get-Content work\SUPERVISOR_REPORT.md
```

There should be one copyable prompt such as
`work/outbox/RW-0001-to-codex-api.md` for every dispatched worker. The task id
and exact acceptance criteria are in that file; do not invent a second prompt.

### Step B.5 — optionally automate the worker handoff

Manual sessions can follow Step C. If a worker runtime has a local CLI or an
adapter, configure it once so ReleaseWitness can pull and invoke it automatically:

```powershell
relwit agent register --id codex-api --role worker `
  --scope src/backend --scope tests/backend `
  --runner-arg=python `
  --runner-arg=tools/codex_worker_adapter.py `
  --runner-arg=--assignment `
  --runner-arg={assignment_path} `
  --runner-timeout 3600

relwit worker run --agent codex-api --max-tasks 1
```

The runner is an argv list, not a shell string, and must contain the
`{assignment_path}` placeholder. The adapter runs from the project root, reads
the assignment, performs the work and submits `task report`. ReleaseWitness records
bounded sanitized runner evidence with a local diagnostic spool reference and
creates a failed report if the adapter exits without one. An optional argv-only
preflight can return `ready`, `unavailable`, `misconfigured`, `no_target` or
`unknown`; only an explicit `ready` result allows the task to become
`in_progress`.
The default remains manual/no-runner, so configuring this is an explicit
permission boundary. Keep `--max-tasks` and `--wait-seconds` finite.

### Step C — start the Codex worker

Open a separate Codex session in `F:\dev\DemoStore` and send:

```text
Use $relwit-worker in F:\dev\DemoStore.
You are the worker session codex-api. Read AGENTS.md, knowledge/INDEX.md and
the generated assignment in work/agents/codex-api/inbox/.
Run `relwit worker pull --agent codex-api`, implement only the
claimed scope, run every requested check, and report with `task report`.
Do not edit another worker's scope. If the assignment is ambiguous or blocked,
report `blocked` with the concrete reason instead of guessing.
```

The worker pull command changes the assigned task from `assigned` to
`in_progress` and prints the assignment. When finished, the worker reports with
the task id shown in that assignment:

```powershell
relwit task report RW-XXXX `
  --agent codex-api `
  --result completed `
  --summary "Backend slice implemented and verified" `
  --next-action "Supervisor or reviewer checks the evidence" `
  --file src/backend/example.py `
  --check "python -m unittest discover -s tests -v: pass"
```

The CLI writes the handover to the configured Markdown surfaces. The worker
does not need to edit `work/registry.json` or manually maintain completed logs.

### Step D — start the Claude Code worker

Claude Code must be a local coding session, not a browser chat. On Windows, use
the supported Git Bash or WSL setup, then start it in the same repository:

```bash
cd /f/dev/DemoStore
claude
```

Send this prompt:

```text
You are the worker session claude-web in this repository.
Read AGENTS.md, knowledge/INDEX.md, and
.agents/skills/relwit-worker/SKILL.md. Read the assignment in
work/agents/claude-web/inbox/, then run:

relwit worker pull --agent claude-web

Implement only the claimed frontend scope. Run the requested checks and submit
the result with `relwit task report <task-id> --agent claude-web ...`.
Never mark a task done by editing JSON; if blocked, report blocked with evidence.
```

If Claude Code is not loading project instructions automatically, keep the
explicit `AGENTS.md` and `SKILL.md` paths in the prompt. A project-level
`CLAUDE.md` can also import the repository instructions, but it is an adapter
convenience; the ReleaseWitness source of truth remains `AGENTS.md`, the registry and
the Markdown contracts.

### Step E — start the Antigravity worker

In Antigravity 2.0 or the Antigravity IDE/CLI:

1. Create or open a Project for `F:\dev\DemoStore`.
2. Use local/shared-folder mode for this simple example so all sessions see the
   same `work/` state.
3. Start an agent and send the prompt below.

```text
You are the worker session antigravity-qa in the attached project.
Read AGENTS.md, knowledge/INDEX.md, and
.agents/skills/relwit-worker/SKILL.md. Open and follow the assignment in
work/agents/antigravity-qa/inbox/. Then run:

relwit worker pull --agent antigravity-qa

Work only inside the claimed tests/qa and docs/qa scope. Use browser evidence
only when the assignment requests it. Run the requested checks and report with
the exact task id using `task report`. Do not deploy or change permissions.
```

Antigravity can also load a skill directory through its SDK `skills_paths`; if
you build a custom Antigravity runner, point it at
`.agents/skills/relwit-worker` and keep the same CLI/report contract. The
runtime-specific tool surface may differ, but the mailbox protocol does not.

### Step F — let the supervisor close the loop

After workers report, return to the supervisor session and run:

```powershell
relwit supervisor cycle --run-qa
Get-Content work\SUPERVISOR_REPORT.md
relwit task list
```

The supervisor ingests reports, checks acceptance and evidence, runs configured
QA, moves valid work toward review, creates a scoped debug task for failures,
and dispatches the next ready tasks. A worker saying “completed” is not enough
to make a task `done`.

The user should normally inspect only:

```text
work/SUPERVISOR_REPORT.md       current health and next action
work/checkpoints/               durable resume point
work/evidence/                  reproducible QA output
work/completed/COMPLETED.md     completed worker handovers
```

### Step G — run the credential-free conformance demo

Before connecting real runtimes, verify the protocol with the included
simulated worker:

```powershell
python examples/multi-agent-demo/run_demo.py
```

The demo creates a temporary project root, registers `demo-worker`, dispatches
one task, pulls it, submits a report, runs supervisor ingest plus QA, and
asserts the assignment mailbox, report inbox, completed logs, supervisor state
and checkpoint. It does not call Codex, Claude, Antigravity or any external
service. A successful run prints:

```text
PASS: RW-0001 assignment -> pull -> report -> ingest -> QA -> checkpoint
```

This is a protocol smoke test, not a substitute for application tests or a
review of a real production change.

### Step H — verify three runtime identities together

When you want proof that a mixed Codex + Claude Code + Antigravity roster can
share one control-plane root, run the multi-runtime harness:

```powershell
python examples/multi-runtime-conformance/run_conformance.py
```

It registers three isolated worker identities (`codex-backend`,
`claude-frontend` and `antigravity-qa`), creates one task per scope, confirms
that automatic routing selects the matching worker, runs every worker through
the opt-in runner bridge and automatic `task report`, then runs supervisor
ingest, QA and checkpoint creation. It uses a temporary project and no vendor
credentials. A successful run prints a `PASS` line naming all three runtimes.

This is a protocol and routing conformance test, not a test of the Codex,
Claude Code or Antigravity product APIs. To replace a simulated runtime with a
real one, keep the generated worker id, configure an adapter with the same
`{assignment_path}` contract, and test one bounded `worker run` before enabling
more tasks. If no adapter/CLI exists, keep using the generated outbox prompt and
the manual pull/report flow.

## 4. What happens to each Markdown file?

| File | Written by | Meaning |
| --- | --- | --- |
| `work/agents/<id>/INBOX.md` | supervisor/CLI | Human-readable assignment index. |
| `work/agents/<id>/inbox/<task>.md` | supervisor/CLI | Full task prompt and acceptance criteria. |
| `work/outbox/<task>-to-<id>.md` | supervisor/CLI | Copyable prompt for an external runtime. |
| `work/agents/<id>/REPORT.md` | worker/CLI | Per-agent handover history. |
| `work/reports/inbox/<report>.md` | worker/CLI | Input for the next supervisor ingest. |
| `work/completed/COMPLETED.md` | worker/CLI | Append-only worker completion log. |
| `work/evidence/runner-*.md` | worker/CLI | Bounded sanitized adapter summary with provenance and local-spool reference. |
| `work/SUPERVISOR_REPORT.md` | supervisor/CLI | Current project health, gates and next action. |
| `work/evidence/<evidence>.md` | supervisor/CLI | Captured test, QA or review evidence. |
| `work/checkpoints/<checkpoint>.md` | supervisor/CLI | Resume context for the next bounded cycle. |

Do not make a worker write directly to another worker's mailbox. Use
`task report`; the CLI fans the report out to the configured Markdown files.

## 5. Shared folder or Git worktree?

ReleaseWitness is not a worktree manager or multi-branch concurrency engine. An
external orchestrator may own branches, worktrees and parallel execution;
ReleaseWitness can verify the resulting repository state. The choice below applies
only when you enable its optional shared-folder supervision workflow.

### Recommended first setup: one shared folder

Use one local checkout when:

- all agents have non-overlapping source scopes;
- the supervisor and workers must see the same `work/registry.json` immediately;
- you are learning the protocol.

The active-writer rule is the safety boundary. For example, backend owns
`src/backend` and frontend owns `src/frontend`; neither claims the whole `src`
tree or the same test file.

### Worktrees: isolation with an important caveat

A Git worktree isolates source files, but it also has its own checkout of the
file-first `work/` ledger. It does not automatically share the supervisor's
`work/registry.json`. Use worktrees only when you have a deliberate process for
returning the worker's report/evidence to the canonical supervisor checkout, or
when the orchestration runtime provides shared state. For the beginner flow,
use the shared folder and narrow scopes.

## 6. Troubleshooting

### `NO_TASK`

The id has no assigned task. Return to the supervisor and run
`relwit supervisor cycle`, then check
`work/outbox/` and `relwit agent list`.

### Scope conflict

Another active writer owns an overlapping path. Do not bypass the check. Ask the
supervisor to serialize the tasks, narrow a scope or use an explicitly managed
worktree.

### The task is `reported`, not `done`

That is expected. `reported` is the worker's claim. The supervisor/reviewer must
check the diff and evidence before closing it.

Only a registered `supervisor`, `reviewer` or `release_gate` identity may record
review evidence or move a reported task through the release gate:

```powershell
relwit task evidence RW-0001 --kind review --agent reviewer --value "Diff and QA pass"
relwit task update RW-0001 --status needs_review --agent reviewer
relwit task update RW-0001 --status done --agent reviewer
```

The assigned worker can report completion and test evidence, but cannot
self-approve or self-close the task. The reviewer may be a different registered
agent from the worker.

The review value must contain a non-empty, repeatable finding or check; an empty
review entry cannot satisfy the release gate.

Use `task report` for worker completion. A direct
`task update --status reported` is rejected so the registry cannot contain a
report-less completion.

The worker must pull or claim the assignment first; reporting directly from
`assigned` is rejected so the activation step cannot be bypassed.

The reviewer must wait for the task to be `reported`; reviewing an active,
unreported task is rejected.

`done` and `cancelled` are terminal states. Reopening them through
`task update` is rejected; create a follow-up task for additional work.

Use `worker`, `explorer`, `planner` or `supervisor` for implementation claims;
`reviewer` and `release_gate` are review-only roles and cannot claim/report
implementation work.

An assigned task must be activated with `worker pull` or `task claim`, not a
direct `task update`. Administrative `planned`, `blocked` and `cancelled`
updates require a review-capable identity. Claims and pulls also require the
agent to be `available`, below `max_active`, and eligible for the task scope
and capabilities.

### The runtime cannot find `$relwit-worker`

Use the direct path instead:

```text
Read .agents/skills/relwit-worker/SKILL.md and follow it.
```

The skill name is a convenience; the Markdown file and CLI protocol are the
portable contract.

### The automatic path is opt-in

ReleaseWitness does not guess how to launch arbitrary external models from a
filesystem. By default it creates durable assignments for a native Codex
subagent, Claude Code session, Antigravity agent, another runner or a human to
execute. If you explicitly configure an argv runner for an agent, bounded
`worker run` performs the pull and launch for you. This is an intentional
permission boundary: the adapter still owns vendor-specific invocation and
must submit the report through the CLI.

## 7. First-run checklist

```text
[ ] ReleaseWitness control-plane files are in the target repository root.
[ ] `relwit validate` returns VALID.
[ ] Supervisor has a light goal and the real worker roster.
[ ] Each worker has a unique id, role, capability and non-overlapping scope.
[ ] Supervisor has run one bounded `supervisor cycle`.
[ ] Each worker pulled its assignment before editing.
[ ] Each worker reported through `task report` with files and checks.
[ ] Supervisor ran the next cycle and inspected SUPERVISOR_REPORT.md.
[ ] Production/release work has review, QA, rollback notes and explicit deploy approval.
```

---

## Hướng dẫn thao tác thực tế bằng tiếng Việt

### ReleaseWitness hỗ trợ những agent nào?

ReleaseWitness không khóa vào một nhà cung cấp model. Nó hỗ trợ mọi runtime có thể
truy cập cùng repository, đọc/ghi Markdown, chạy lệnh và tuân thủ scope. Các
role chuẩn là:

| Role | Việc chính |
| --- | --- |
| `supervisor` | Là “bộ não”: hiểu goal, lập roadmap/DAG, giao việc, đọc report, QA và chọn next action. |
| `explorer` | Đọc repository ở chế độ chỉ đọc, tìm đúng file và source anchor. |
| `planner` | Tách goal thành task có dependency, scope và acceptance rõ ràng. |
| `worker` | Nhận đúng một task, sửa trong scope, chạy test và nộp handover. |
| `reviewer` | Review diff, regression, security và test gap. |
| `release_gate` | Kiểm tra evidence, vận hành, rollback và điều kiện release. |

`Codex`, `Claude Code` và `Antigravity` không phải role; chúng là các runtime
để chạy những role trên. Vì vậy bạn có thể dùng Codex làm supervisor, Claude
Code làm frontend worker và Antigravity làm QA worker trong cùng một dự án.

### Người dùng phải làm gì?

1. Đặt control plane của ReleaseWitness trong root của repository dự án.
2. Chạy `init` và `validate`.
3. Đăng ký từng session thật bằng một id duy nhất.
4. Gửi một goal ngắn cho supervisor.
5. Chạy một cycle để supervisor tạo task và prompt trong `work/outbox/`.
6. Mở từng runtime worker trong cùng thư mục, cho worker chạy `worker pull`.
7. Để worker sửa code và dùng `task report`, hoặc cấu hình runner một lần rồi
   chạy `worker run --agent <id> --max-tasks 1` để tự pull/gọi adapter.
8. Hỏi supervisor hoặc chạy cycle tiếp theo để ingest report, QA, review và giao
   phần việc còn lại.

Bạn không cần tự viết lại prompt assignment. Prompt đầy đủ nằm trong
`work/outbox/`; phần prompt mẫu ở trên chỉ là “bootstrap” để runtime biết phải
đọc file nào và dùng worker id nào. Với runner đã cấu hình, adapter nhận
`{assignment_path}`. ReleaseWitness tự ghi runner summary đã sanitize, kèm local spool
reference, và tạo failed report nếu adapter không report. Preflight argv-only tùy
chọn phải trả về trạng thái rõ ràng; chỉ `ready` mới cho task chuyển sang
`in_progress`.

### Chạy demo không cần credential

Trước khi kết nối Codex, Claude Code hoặc Antigravity, chạy:

```powershell
python examples/multi-agent-demo/run_demo.py
```

Demo dùng worker giả lập nhưng vẫn gọi CLI thật để kiểm tra `dispatch`,
`worker pull`, `task report`, supervisor ingest, QA và checkpoint. Nó tạo
project tạm, không gọi API bên ngoài và tự assert mailbox, report, completed log
và supervisor state. Dòng `PASS` nghĩa là protocol nền đã chạy thông suốt;
không có nghĩa application production đã được QA.

### Kiểm tra đồng thời ba runtime

Muốn kiểm chứng roster Codex + Claude Code + Antigravity dùng chung một
control-plane root, chạy thêm:

```powershell
python examples/multi-runtime-conformance/run_conformance.py
```

Harness đăng ký ba identity độc lập (`codex-backend`, `claude-frontend`,
`antigravity-qa`), tạo task ở ba scope không chồng lấn, kiểm tra supervisor tự
route đúng worker, rồi chạy đủ `worker pull`, `task report`, ingest, QA và
checkpoint. Harness dùng project tạm và không gọi credential/API của vendor.

Đây là bằng chứng conformance của protocol và routing, không phải bằng chứng
API Codex/Claude/Antigravity hoạt động. Khi thay worker giả lập bằng runtime
thật, giữ nguyên worker id, mở runtime trong cùng project, gửi prompt tương ứng
trong `work/outbox/` và để session thật chạy cùng chu trình pull/report.

### Nếu dùng cả Codex, Claude và Antigravity

- **Codex:** mở hai session trở lên trong cùng project: một supervisor và các
  worker. Dùng `$relwit` cho supervisor, `$relwit-worker` cho worker. Các
  profile role mẫu nằm trong `.codex/agents/`.
- **Claude Code:** mở `claude` trong đúng repository local, không dùng Claude.ai
  chat làm worker. Nếu skill alias không được nhận, yêu cầu đọc trực tiếp
  `.agents/skills/relwit-worker/SKILL.md`, rồi chạy CLI.
- **Antigravity:** tạo Project trỏ đến đúng thư mục repository, chọn local mode
  cho mô hình shared-folder đơn giản, mở agent và đưa prompt worker trực tiếp.
  Với SDK, cấu hình `skills_paths` trỏ vào thư mục skill.

Tất cả runtime phải dùng chung các file `work/`. Không cho hai worker cùng sửa
một file; ReleaseWitness sẽ từ chối active-writer scope bị chồng lấn.

### Khi nào người dùng cần can thiệp?

Chỉ cần quyết định khi supervisor gặp goal mơ hồ, thiếu quyền truy cập, scope
xung đột, lỗi lặp lại không có giả thuyết mới, hoặc cần deploy/thay đổi dữ liệu,
secret và quyền. Những trường hợp còn lại supervisor tiếp tục theo từng cycle
hữu hạn và luôn ghi lại next action trong `work/SUPERVISOR_REPORT.md`.

### Lệnh kiểm tra tối thiểu

```powershell
relwit validate
relwit agent list
relwit supervisor cycle --run-qa
relwit task list
Get-Content work\SUPERVISOR_REPORT.md
```

Nếu bạn chỉ muốn nhớ một điều: **mở mọi agent trong cùng control-plane root,
đặt tên worker duy nhất, để supervisor tạo prompt, và luôn để worker pull/report
qua CLI thay vì sửa registry bằng tay.**
