# 평일 모의 운행 — 작업 스케줄러가 08:50 에 부른다(로그온 상태여야 돈다: Interactive).
#   strategy_live.py --paper-order 를 config.yaml 설정으로 돌린다. 실행기는 09:01 첫 봉 마감까지 스스로 기다린다.
#   끝나면(until) 진행 중 주문을 취소·정리하고 잔고를 대조한 뒤 요약을 기록한다(실행기 안에서).
#   멈추려면 state\stop 파일을 만든다(강제 종료 대신). 실전 주문 경로는 없다. 종목 코드는 예시이며 추천이 아니다.
# 기록(logs\): run_daily_YYYYMMDD.log(이 스크립트) · strategy_live_YYYYMMDD.out.log(파이썬 출력) · strategy_live_YYYYMMDD.jsonl(실행기)
# 손으로: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_daily.ps1 -SelfTest  (API 호출 없음)
#         ... -Once  (지금 닫힌 봉 한 번, dry-run)   ... -Probe  (모의 점검 paper_probe.py, 장중만)
#         ... -Sim   (실시간 시뮬레이션 sim_live.py — 주문 안 보냄, 기록 logs\sim\)
#         ... -Ext   (바깥 신호 문을 연다 = 실행기에 --ext. 모의는 state\ext\, 시뮬레이션은 state\sim\ext\. 기본 꺼짐)
#         ... -Judge (AI 판단 프로세스 judge_run.py 를 함께 띄운다 — 실행기가 잠금 파일을 잡은 뒤에 시작, --until 은 실행기와 같게.
#                     문을 쓰려면 -Ext 도 함께. 판단에 줄 다른 인자는 -JudgeExtra:--값 꼴, 기록 logs\judge_run_YYYYMMDD.out.log.
#                     -JudgeOnly · -Lock · -Since 는 이 스크립트가 스스로를 부를 때만 쓰는 내부 인자)
#   시작 전에 문 폴더를 한 번 정리한다(control.ext_write cleanup — 앞선 날의 inbox_done · 만료된 gates · review_requests, 오늘 것은 남김).
#   --로 시작하는 다른 인자는 -Extra:--값 꼴로 넘긴다(따옴표만 치면 PowerShell 이 매개변수로 읽어 시작 전에 실패).
param([switch]$Once, [switch]$SelfTest, [switch]$Probe, [switch]$Sim, [switch]$Ext, [switch]$Judge, [string]$Start = '', [string]$Until = '', [string]$Extra = '', [string]$JudgeExtra = '', [switch]$JudgeOnly, [string]$Lock = '', [string]$Since = '')

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
# -Start · -Until 은 준 때만 실행기에 넘긴다 — 주지 않으면 실행기가 config.yaml 의 start · until 을 쓴다(늘 넘기면 설정 값이 무시된다).
# 이 스크립트가 스스로 쓰는 끝 시각(다시 시도 판단 · 판단 프로세스)은 준 값 → config.yaml 의 until → 15:20 순서로 정한다.
$startArg = if ($Start) { " --start $Start" } else { '' }
$untilArg = if ($Until) { " --until $Until" } else { '' }
if (-not $Until) {
    $Until = '15:20'
    $cfgPath = Join-Path $root 'config.yaml'
    if (Test-Path $cfgPath) {
        $m = Select-String -Path $cfgPath -Pattern '^\s*until\s*:\s*["'']?(\d{1,2}:\d{2})' -Encoding UTF8 | Select-Object -First 1
        if ($m) { $Until = $m.Matches[0].Groups[1].Value.PadLeft(5, '0') }
    }
}
$py = Join-Path $root '.venv\Scripts\python.exe'
Set-Location $root
New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs') | Out-Null
$day = Get-Date -Format 'yyyyMMdd'
$runLog = Join-Path $root "logs\run_daily_$day.log"
$outLog = Join-Path $root "logs\strategy_live_$day.out.log"

function Log($m) { "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $m" | Out-File -Append -Encoding utf8 $runLog }
function RunPy($argLine, $target) {
    # cmd 로 감싸 stdout·stderr 를 한 파일에 이어 붙인다. -u: 줄마다 바로 써서 장중에도 읽을 수 있게
    & cmd.exe /d /c "`"`"$py`" -X utf8 -u $argLine >> `"$target`" 2>&1`""
    return $LASTEXITCODE
}
function CleanDoor($target, $logFile) {
    # 문 폴더 정리 한 번(실행기 시작 전). 실패해도 운행은 계속한다
    $c = RunPy "-m control.ext_write cleanup $target" $logFile
    Log "문 정리($target) exit=$c"
}
function StartJudge($lockRel) {
    # 판단 프로세스를 따로 띄운다 — 이 스크립트를 -JudgeOnly 로 다시 불러, 실행기가 잠금을 잡을 때까지 기다린 뒤 시작한다
    $since = (Get-Date).AddSeconds(-2).Ticks
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', "`"$PSCommandPath`"", '-JudgeOnly', '-Until', $Until, '-Lock', $lockRel, '-Since', "$since")
    if ($JudgeExtra) { $argList += "-JudgeExtra:`"$JudgeExtra`"" }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -WindowStyle Hidden | Out-Null
        Log "판단 프로세스 띄움(실행기 잠금 $lockRel 을 기다린 뒤 시작)"
    } catch {
        Log "판단 프로세스를 띄우지 못함: $($_.Exception.GetType().Name)"
    }
}

if ($JudgeOnly) {
    # 실행기가 잠금 파일을 새로 잡을 때까지(최대 15분) 기다린다 — 시황 판단이 실행기 시작 뒤에 문을 쓰게
    $judgeLog = Join-Path $root "logs\judge_run_$day.out.log"
    $lockPath = Join-Path $root $Lock
    $t0 = Get-Date
    $after = if ($Since) { New-Object DateTime ([long]$Since) } else { $t0 }   # 부른 쪽이 띄운 시각 — 그 뒤에 새로 잡힌 잠금만 본다
    $ready = $false
    while (((Get-Date) - $t0).TotalSeconds -lt 900) {
        if ((Test-Path $lockPath) -and ((Get-Item $lockPath).LastWriteTime -ge $after)) { $ready = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { Log "판단: 실행기 잠금($Lock)이 15분 안에 잡히지 않아 띄우지 않음"; exit 0 }
    Log "판단 실행: python -X utf8 -u scripts/judge_run.py --until $Until $JudgeExtra"
    $code = RunPy "scripts/judge_run.py --until $Until $JudgeExtra" $judgeLog
    Log "판단 종료 exit=$code (3 = 이미 다른 판단 프로세스가 돎)"
    exit $code
}

$extArg = if ($Ext) { ' --ext' } else { '' }
Log "=== run_daily.ps1 시작  once=$Once  selftest=$SelfTest  probe=$Probe  sim=$Sim  ext=$Ext  judge=$Judge  ps=$($PSVersionTable.PSVersion)"
if (-not (Test-Path $py)) { Log "가상환경 파이썬이 없음: .venv — README 설치 순서대로 만드세요"; exit 2 }

if ($SelfTest) {
    $code = RunPy "scripts/selftest.py" $outLog
    Log "selftest exit=$code (내용은 strategy_live_$day.out.log)"
    exit $code
}

if ($Probe) {
    $probeLog = Join-Path $root "logs\paper_probe_$day.out.log"
    $code = RunPy "scripts/paper_probe.py $Extra" $probeLog
    Log "paper_probe exit=$code (내용은 paper_probe_$day.out.log · .jsonl)"
    exit $code
}

if ($Sim) {
    $simLog = Join-Path $root "logs\sim\sim_live_$day.out.log"
    New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs\sim') | Out-Null
    CleanDoor 'sim' $simLog
    if ($Judge) { StartJudge 'state\sim\run_paper.lock' }
    $tries = 0
    while ($true) {
        $tries++
        $t0 = Get-Date
        Log "시뮬레이션 실행 $tries 회: python -X utf8 -u scripts/sim_live.py$extArg"
        $code = RunPy "scripts/sim_live.py$startArg$untilArg$extArg $Extra" $simLog
        $sec = [int]((Get-Date) - $t0).TotalSeconds
        Log "시뮬레이션 종료 exit=$code  걸린 시간 ${sec}초"
        if ($code -ne 0 -and $sec -lt 600 -and (Get-Date).ToString('HH:mm') -lt $Until -and $tries -lt 3) { Start-Sleep -Seconds 60; continue }
        break
    }
    exit $code
}

if (Test-Path (Join-Path $root 'state\stop')) { Log "state\stop 이 있어 시작하지 않음"; exit 0 }

$argLine = if ($Once) { "scripts/strategy_live.py --once$extArg" } else { "scripts/strategy_live.py --paper-order$startArg$untilArg$extArg $Extra" }
if (-not $Once) {
    CleanDoor 'paper' $outLog
    if ($Judge) { StartJudge 'state\run_paper.lock' }
}
$tries = 0
while ($true) {
    $tries++
    $t0 = Get-Date
    Log "실행 $tries 회: python -X utf8 -u $argLine"
    $code = RunPy $argLine $outLog
    $sec = [int]((Get-Date) - $t0).TotalSeconds
    Log "python 종료 exit=$code  걸린 시간 ${sec}초"
    $early = ($sec -lt 600) -and ((Get-Date).ToString('HH:mm') -lt $Until)
    # 시작 10분 안 비정상 종료(토큰·네트워크)만 60초 뒤 최대 3회 — 재시작하면 OMS 가 state 파일에서 이어 받는다
    if ($code -ne 0 -and -not $Once -and $early -and $tries -lt 3) { Log "비정상 종료 — 60초 뒤 다시 시도"; Start-Sleep -Seconds 60; continue }
    break
}
Log "=== 끝 (요약은 strategy_live_$day.jsonl 의 stop 사건)"
exit $code                                     # 파이썬 종료 코드를 그대로 — 실패가 작업 스케줄러에 성공(0)으로 남지 않게
