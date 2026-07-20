$ErrorActionPreference = 'SilentlyContinue'
$log = 'E:\Claude Projects\Projects\Jobhunter AI\dashboard\errors\_watch_always.log'
$since = 0
$lastState = ''
function Stamp($s) {
  $line = "$(Get-Date -Format 'HH:mm:ss') $s"
  Add-Content -Path $log -Value $line -Encoding utf8
}
Add-Content -Path $log -Value '' -Encoding utf8
Stamp 'ALWAYS_ON_DETACHED'
try {
  $boot = (Invoke-WebRequest -Uri 'http://127.0.0.1:5959/api/events?since=0' -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
  $since = $boot.next
} catch {}
for ($i = 0; $i -lt 7200; $i++) {
  try {
    $raw = (Invoke-WebRequest -Uri "http://127.0.0.1:5959/api/events?since=$since" -UseBasicParsing -TimeoutSec 5).Content
    $data = $raw | ConvertFrom-Json
    if ($null -eq $data.next) { Start-Sleep -Seconds 6; continue }
    if ($data.next -lt $since) { $since = 0 }
    $since = [int]$data.next
    foreach ($ev in @($data.events)) {
      $msg = $ev.detail.message
      if (-not $msg) { $msg = $ev.detail.error }
      if (-not $msg) { $msg = $ev.detail.label }
      $line = "[$($ev.type)/$($ev.status)] $($ev.agent_id) $msg"
      $isHard = ($ev.type -eq 'awaiting_retry') -or ($ev.type -eq 'error') -or ($ev.type -eq 'run' -and $ev.status -in @('done','failed','aborted'))
      $isSoft = ($ev.type -eq 'llm' -and $ev.status -eq 'retrying') -or ($ev.type -eq 'task') -or ($ev.type -eq 'run' -and $ev.status -eq 'started')
      if ($isHard -or $isSoft) { Stamp $line }
      if ($isHard) { Stamp "ALERT $line" }
    }
    $st = ((Invoke-WebRequest -Uri 'http://127.0.0.1:5959/api/run/status' -UseBasicParsing -TimeoutSec 3).Content | ConvertFrom-Json)
    $sig = "$($st.live)|$($st.server.status)|$($st.state.status)|$($st.state.error)"
    if ($sig -ne $lastState) {
      Stamp "STATE live=$($st.live) server=$($st.server.status) state=$($st.state.status) err=$($st.state.error)"
      if ($st.state.status -eq 'awaiting_retry') { Stamp "ALERT awaiting_retry $($st.state.error)" }
      if (-not $st.live -and $st.server.status -in @('done','failed','aborted')) { Stamp "ALERT RUN_ENDED status=$($st.server.status) exit=$($st.server.exit_code)" }
      $lastState = $sig
    }
  } catch { Stamp "poll_err $($_.Exception.Message)" }
  Start-Sleep -Seconds 6
}
Stamp 'ALWAYS_ON_END'
