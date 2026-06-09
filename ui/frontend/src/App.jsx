import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine
} from 'recharts'

// ── Constants ──────────────────────────────────────────────────────────────
const CODECS    = ['H.264', 'H.265', 'VP9']
const PROTOCOLS = ['UDP/RTP (WebRTC)', 'QUIC', 'SCReAM']
const GAMES     = ['Fortnite', 'Foraz', 'Kombat']
const FPS_OPTS  = ['30', '60', '90', '120']
const RES_OPTS  = ['640×480', '1280×720', '1920×1080']
const TOPO_OPTS = ['linear', 'tree', 'single']

// ── Styles ─────────────────────────────────────────────────────────────────
const s = {
  app: {
    maxWidth: 1200, margin: '0 auto', padding: '16px',
    display: 'flex', flexDirection: 'column', gap: 10,
  },
  header: {
    display: 'flex', alignItems: 'center', gap: 12,
    padding: '12px 0 4px',
    borderBottom: '0.5px solid rgba(255,255,255,0.08)',
    marginBottom: 4,
  },
  panel: {
    background: '#161b22',
    border: '0.5px solid rgba(255,255,255,0.1)',
    borderRadius: 12, overflow: 'hidden',
  },
  panelHead: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '9px 14px',
    borderBottom: '0.5px solid rgba(255,255,255,0.07)',
    background: 'rgba(255,255,255,0.02)',
  },
  panelTitle: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12, fontWeight: 500, color: '#e6edf3',
    letterSpacing: '-0.01em',
  },
  badge: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 9, fontWeight: 500, letterSpacing: '0.06em',
    textTransform: 'uppercase', padding: '2px 7px',
    borderRadius: 100,
  },
  body: { padding: 14 },
  grid2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 },
  grid3: { display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 },
  grid4: { display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 8 },
  field: { display: 'flex', flexDirection: 'column', gap: 4 },
  label: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 10, fontWeight: 500, color: '#8b949e',
    textTransform: 'uppercase', letterSpacing: '0.04em',
  },
  fieldRow: { display: 'flex', alignItems: 'center', gap: 6 },
  unit: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 10, color: '#484f58', whiteSpace: 'nowrap',
  },
  divider: {
    display: 'flex', alignItems: 'center', gap: 8,
    marginBottom: 8, marginTop: 4,
  },
  dividerText: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 10, color: '#484f58', whiteSpace: 'nowrap',
  },
  dividerLine: { flex: 1, height: 0.5, background: 'rgba(255,255,255,0.07)' },
  chipGroup: { display: 'flex', flexWrap: 'wrap', gap: 5 },
  chip: {
    padding: '4px 11px', borderRadius: 100, cursor: 'pointer',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 11, fontWeight: 500,
    border: '0.5px solid rgba(255,255,255,0.12)',
    background: 'transparent', color: '#8b949e',
    transition: 'all 0.15s', userSelect: 'none',
  },
  logWin: {
    background: '#0d1117', borderRadius: 8,
    border: '0.5px solid rgba(255,255,255,0.06)',
    padding: '8px 10px', fontFamily: "'JetBrains Mono', monospace",
    fontSize: 11, maxHeight: 80, overflowY: 'auto',
  },
  statusBar: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '7px 14px',
    borderTop: '0.5px solid rgba(255,255,255,0.07)',
    background: 'rgba(0,0,0,0.2)',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 11, color: '#8b949e',
  },
  metricCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '0.5px solid rgba(255,255,255,0.08)',
    borderRadius: 8, padding: '8px 10px',
  },
  metricLabel: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 10, color: '#484f58', marginBottom: 2,
  },
  metricValue: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 20, fontWeight: 500, color: '#e6edf3',
  },
  chartCard: {
    background: 'rgba(255,255,255,0.02)',
    border: '0.5px solid rgba(255,255,255,0.07)',
    borderRadius: 8, padding: 10,
  },
  chartTitle: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 10, color: '#8b949e', marginBottom: 6,
  },
}

// ── Topology SVG ──────────────────────────────────────────────────────────
function TopologyViz({ switches, bw, delay, bnBw, loss }) {
  const n = Math.max(1, Math.min(8, parseInt(switches) || 2))
  const nodeW = 50, linkW = 40, pad = 20
  const totalW = pad * 2 + nodeW * 2 + (n * (nodeW + linkW)) + linkW
  const h = 80

  const nodes = []
  const links = []

  // H0
  nodes.push({ x: pad + nodeW / 2, label: 'H0', type: 'host', sub: 'server' })
  // Switches
  for (let i = 1; i <= n; i++) {
    nodes.push({
      x: pad + nodeW + linkW + (i - 1) * (nodeW + linkW) + nodeW / 2,
      label: `S${i}`, type: 'switch', sub: '',
    })
  }
  // H1
  nodes.push({
    x: pad + nodeW + linkW + n * (nodeW + linkW) + nodeW / 2,
    label: 'H1', type: 'host', sub: 'player',
  })

  for (let i = 0; i < nodes.length - 1; i++) {
    const isBottleneck = i === nodes.length - 2
    links.push({
      x1: nodes[i].x + nodeW / 2,
      x2: nodes[i + 1].x - nodeW / 2,
      bottleneck: isBottleneck,
      label: isBottleneck ? `${bnBw}M/${loss}%loss` : `${bw}M/${delay}ms`,
    })
  }

  return (
    <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.2)', borderRadius: 8, padding: '8px 0', marginBottom: 10 }}>
      <svg width={Math.max(totalW, 400)} height={h} style={{ display: 'block', margin: '0 auto' }}>
        {links.map((lk, i) => (
          <g key={i}>
            <line x1={lk.x1} y1={32} x2={lk.x2} y2={32}
              stroke={lk.bottleneck ? '#f85149' : 'rgba(255,255,255,0.2)'} strokeWidth={lk.bottleneck ? 2 : 1} />
            <text x={(lk.x1 + lk.x2) / 2} y={20} textAnchor="middle"
              fill={lk.bottleneck ? '#f85149' : '#484f58'}
              fontSize={9} fontFamily="JetBrains Mono, monospace">{lk.label}</text>
          </g>
        ))}
        {nodes.map((nd, i) => (
          <g key={i}>
            <rect x={nd.x - nodeW / 2} y={20} width={nodeW} height={24} rx={5}
              fill={nd.type === 'host' ? 'rgba(56,139,253,0.15)' : 'rgba(255,255,255,0.04)'}
              stroke={nd.type === 'host' ? 'rgba(56,139,253,0.4)' : 'rgba(255,255,255,0.12)'}
              strokeWidth={0.5} />
            <text x={nd.x} y={36} textAnchor="middle"
              fill={nd.type === 'host' ? '#388bfd' : '#8b949e'}
              fontSize={11} fontFamily="JetBrains Mono, monospace" fontWeight={500}>{nd.label}</text>
            <text x={nd.x} y={56} textAnchor="middle"
              fill="#484f58" fontSize={9} fontFamily="JetBrains Mono, monospace">{nd.sub}</text>
          </g>
        ))}
      </svg>
    </div>
  )
}

// ── Chip selector ──────────────────────────────────────────────────────────
function ChipGroup({ options, value, onChange, color }) {
  const colors = {
    blue:  { active: { background: 'rgba(56,139,253,0.15)', border: '0.5px solid #388bfd', color: '#79c0ff' } },
    green: { active: { background: 'rgba(63,185,80,0.12)',  border: '0.5px solid #3fb950', color: '#56d364' } },
    amber: { active: { background: 'rgba(210,153,34,0.12)', border: '0.5px solid #d29922', color: '#e3b341' } },
  }
  return (
    <div style={s.chipGroup}>
      {options.map(opt => (
        <span key={opt}
          style={{ ...s.chip, ...(value === opt ? colors[color || 'blue'].active : {}) }}
          onClick={() => onChange(opt)}>{opt}</span>
      ))}
    </div>
  )
}

// ── Game Canvas ────────────────────────────────────────────────────────────
function LiveFrame({ running }) {
  // During a real run, poll the backend for the newest received game frame and
  // draw it over the procedural canvas. Hidden until a real frame loads, so the
  // canvas shows during Mininet startup or in demo mode (no /api/frame).
  const [src, setSrc] = useState(null)
  const [ok,  setOk]  = useState(false)
  useEffect(() => {
    if (!running) { setOk(false); setSrc(null); return }
    const id = setInterval(() => setSrc(`/api/frame?ts=${Date.now()}`), 120)
    return () => clearInterval(id)
  }, [running])
  if (!running) return null
  return (
    <img
      src={src || undefined}
      alt="live game stream"
      onLoad={() => setOk(true)}
      onError={() => setOk(false)}
      style={{
        position: 'absolute', inset: 0, width: '100%', height: '100%',
        objectFit: 'cover', zIndex: 1, display: ok ? 'block' : 'none',
      }}
    />
  )
}


function GameCanvas({ running, codec, proto, fps, loss }) {
  const canvasRef = useRef(null)
  const rafRef    = useRef(null)
  const tRef      = useRef(0)
  const hueRef    = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')

    const draw = () => {
      tRef.current += 0.016
      hueRef.current = (hueRef.current + 0.3) % 360
      const t = tRef.current
      const w = canvas.width, h = canvas.height

      ctx.fillStyle = '#050810'
      ctx.fillRect(0, 0, w, h)

      // Stars
      for (let i = 0; i < 40; i++) {
        ctx.fillStyle = `rgba(255,255,255,${0.2 + Math.sin(t * 0.5 + i) * 0.15})`
        ctx.beginPath()
        ctx.arc((Math.sin(i * 137.5) * 0.5 + 0.5) * w,
          ((Math.cos(i * 83.1) * 0.5 + 0.5)) * h, 1, 0, Math.PI * 2)
        ctx.fill()
      }
      // Ground
      ctx.fillStyle = '#1a3a1a'
      ctx.fillRect(0, h * 0.82, w, h * 0.18)

      // Enemies
      for (let i = 0; i < 3; i++) {
        const ex = ((t * 0.4 + i * 0.4) % 1.2 - 0.1) * w
        const ey = h * (0.35 + i * 0.12)
        ctx.fillStyle = `hsl(${(hueRef.current + i * 120) % 360},70%,55%)`
        ctx.fillRect(ex - 8, ey - 16, 16, 20)
        ctx.beginPath()
        ctx.arc(ex, ey - 22, 8, 0, Math.PI * 2)
        ctx.fill()
      }
      // Player
      const px = (Math.sin(t * 0.6) * 0.25 + 0.5) * w
      const py = h * 0.77
      ctx.fillStyle = '#4facfe'
      ctx.fillRect(px - 9, py - 20, 18, 26)
      ctx.fillStyle = '#ffe082'
      ctx.beginPath()
      ctx.arc(px, py - 28, 10, 0, Math.PI * 2)
      ctx.fill()
      // Bullets
      for (let i = 0; i < 2; i++) {
        const bx = px + Math.sin(t * 5 + i * 1.2) * 40
        const by = py - 30 - ((t * 120 + i * 50) % h * 0.7)
        if (by > 0) {
          ctx.fillStyle = '#ffcc44'
          ctx.fillRect(bx, by, 3, 10)
        }
      }
      // Glitch if high loss
      if (parseFloat(loss) > 3 && Math.random() < 0.04) {
        ctx.fillStyle = `rgba(0,0,0,${0.3 + Math.random() * 0.4})`
        ctx.fillRect(Math.random() * w, Math.random() * h, Math.random() * 100 + 20, Math.random() * 8 + 2)
      }
      // HUD
      ctx.fillStyle = 'rgba(0,0,0,0.55)'
      ctx.fillRect(4, 4, 210, 18)
      ctx.fillStyle = '#7eb6ff'
      ctx.font = '10px JetBrains Mono, monospace'
      ctx.fillText(`${codec} | ${proto.split('(')[0].trim()} | ${fps}fps`, 8, 16)

      if (running) rafRef.current = requestAnimationFrame(draw)
    }

    if (running) {
      rafRef.current = requestAnimationFrame(draw)
    } else {
      ctx.fillStyle = '#050810'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
    }

    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [running, codec, proto, fps, loss])

  return (
    <canvas ref={canvasRef} width={520} height={292}
      style={{ width: '100%', height: 'auto', borderRadius: 8, display: 'block' }} />
  )
}

// ── Tooltip for charts ─────────────────────────────────────────────────────
const ChartTooltip = ({ active, payload, label, unit }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: '#21262d', border: '0.5px solid rgba(255,255,255,0.14)',
      borderRadius: 6, padding: '6px 10px',
      fontFamily: "'JetBrains Mono',monospace", fontSize: 11,
    }}>
      <div style={{ color: '#8b949e', marginBottom: 2 }}>{label}s</div>
      <div style={{ color: '#e6edf3', fontWeight: 500 }}>
        {payload[0].value}{unit}
      </div>
    </div>
  )
}

// ── Main App ───────────────────────────────────────────────────────────────
export default function App() {
  // Config state
  const [nSwitches, setNSwitches] = useState(2)
  const [nDevices,  setNDevices]  = useState(2)
  const [topoType,  setTopoType]  = useState('linear')
  const [delay,     setDelay]     = useState(20)
  const [jitter,    setJitter]    = useState(5)
  const [bw,        setBw]        = useState(100)
  const [pktLoss,   setPktLoss]   = useState(1)
  const [bnBw,      setBnBw]      = useState(10)
  const [codec,     setCodec]     = useState('H.264')
  const [proto,     setProto]     = useState('UDP/RTP (WebRTC)')
  const [game,      setGame]      = useState('Fortnite')
  const [fps,       setFps]       = useState('30')
  const [res,       setRes]       = useState('640×480')
  const [duration,  setDuration]  = useState(30)
  const [cfgPath,   setCfgPath]   = useState('CGReplay/config/config.yaml')

  // Simulation state
  const [running,   setRunning]   = useState(false)
  const [elapsed,   setElapsed]   = useState(0)
  const [status,    setStatus]    = useState('ready — configure topology and click start')
  const [logs,      setLogs]      = useState([{ level: 'info', msg: '$ waiting for simulation start...' }])
  const [commands,  setCommands]  = useState([])
  const [metrics,   setMetrics]   = useState({ rt: null, vmaf: null, ssim: null, psnr: null })
  const [rtData,    setRtData]    = useState([])
  const [vmafData,  setVmafData]  = useState([])
  const [ssimData,  setSsimData]  = useState([])
  const [psnrData,  setPsnrData]  = useState([])
  const [wsConn,    setWsConn]    = useState(false)

  const wsRef  = useRef(null)
  const logRef = useRef(null)

  // ── WebSocket connection ─────────────────────────────────────────────────
  useEffect(() => {
    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const host  = window.location.hostname === 'localhost' ? 'localhost:8000' : window.location.host
      const ws    = new WebSocket(`${proto}://${host}/ws`)

      ws.onopen = () => {
        setWsConn(true)
        addLog('info', '> WebSocket connected to backend')
      }
      ws.onclose = () => {
        setWsConn(false)
        setTimeout(connect, 2000)
      }
      ws.onerror = () => {
        addLog('warn', '> Backend not reachable — running in demo mode')
      }
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data)
        handleMessage(msg)
      }
      wsRef.current = ws
    }
    connect()
    return () => wsRef.current?.close()
  }, [])

  const handleMessage = useCallback((msg) => {
    if (msg.type === 'tick') {
      setElapsed(msg.t)
      setMetrics(msg.metrics)
      const pt = { t: msg.t, ...msg.metrics }
      setRtData(d   => [...d.slice(-499), { t: msg.t, v: msg.metrics.rt   }])
      setVmafData(d => [...d.slice(-499), { t: msg.t, v: msg.metrics.vmaf }])
      setSsimData(d => [...d.slice(-499), { t: msg.t, v: msg.metrics.ssim }])
      setPsnrData(d => [...d.slice(-499), { t: msg.t, v: msg.metrics.psnr }])
      if (msg.command) {
        setCommands(c => [msg.command, ...c.slice(0, 9)])
      }
    } else if (msg.type === 'log') {
      addLog(msg.level, msg.msg)
    } else if (msg.type === 'done') {
      setRunning(false)
      setStatus(`simulation complete — ${msg.msg}`)
    } else if (msg.type === 'stopped') {
      setRunning(false)
      setStatus('simulation stopped by user')
    } else if (msg.type === 'error') {
      addLog('warn', `> Error: ${msg.msg}`)
    }
  }, [])

  const addLog = (level, msg) => {
    setLogs(l => [...l.slice(-20), { level, msg }])
    setTimeout(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, 50)
  }

  // ── Start/stop ───────────────────────────────────────────────────────────
  const handleRun = () => {
    if (running) {
      wsRef.current?.send(JSON.stringify({ action: 'stop' }))
      setRunning(false)
      setStatus('stopping...')
    } else {
      setRunning(true)
      setElapsed(0)
      setRtData([]); setVmafData([]); setSsimData([]); setPsnrData([])
      setCommands([])
      setLogs([])
      setStatus(`running — ${codec} | ${proto.split('(')[0].trim()} | ${game}`)

      const cfg = {
        n_switches: nSwitches, n_devices: nDevices, topo_type: topoType,
        bw, delay, jitter, pkt_loss: pktLoss, bn_bw: bnBw,
        codec, proto, game, fps, res, duration, config_path: cfgPath,
      }

      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ action: 'start', config: cfg }))
      } else {
        // Demo mode — run locally without backend
        runDemoMode(cfg)
      }
    }
  }

  // ── Demo mode (no backend) ───────────────────────────────────────────────
  const demoRef = useRef(null)
  const runDemoMode = (cfg) => {
    addLog('warn', '> Backend offline — running in demo/preview mode')
    addLog('info', `$ mn --topo ${cfg.topo_type},${cfg.n_switches} --link tc,bw=${cfg.bw},delay=${cfg.delay}ms`)
    addLog('ok',   `> Topology: H0 ↔ ${Array.from({length: cfg.n_switches}, (_,i) => `S${i+1}`).join(' ↔ ')} ↔ H1`)
    addLog('info', `> Codec: ${cfg.codec} | Protocol: ${cfg.proto} | Game: ${cfg.game}`)

    const CMDS = ['↑ forward','↓ back','← left','→ right','A attack','B jump','X reload','LT aim','RT fire']
    let t = 0
    demoRef.current = setInterval(() => {
      t++
      const congestion = Math.max(0, 1 - cfg.bn_bw / 100)
      const rng = (a, b) => a + Math.random() * (b - a)
      const m = {
        rt:   Math.round(cfg.delay * 2 + cfg.jitter * 2 + cfg.pkt_loss * 5 + congestion * 80 + rng(-10, 10)),
        vmaf: Math.round(Math.min(100, Math.max(5, 95 - cfg.pkt_loss * 4 - congestion * 40 + rng(-2, 2)))),
        ssim: parseFloat(Math.min(1, Math.max(0.4, 0.97 - cfg.pkt_loss * 0.02 - congestion * 0.15 + rng(-0.01, 0.01))).toFixed(3)),
        psnr: parseFloat(Math.min(50, Math.max(18, 42 - cfg.pkt_loss * 1.5 - congestion * 10 + rng(-1, 1))).toFixed(1)),
      }
      handleMessage({ type: 'tick', t, duration: cfg.duration, metrics: m,
        command: { time: t, cmd: CMDS[Math.floor(Math.random() * CMDS.length)], dir: Math.random() > 0.5 ? 'UP' : 'DN', latency: Math.round(cfg.delay * 2 + rng(0, 20)), dropped: Math.random() < cfg.pkt_loss / 100 } })
      if (t % 5 === 0) addLog(t % 15 === 0 ? 'warn' : 'ok', `> t=${t}s — frames OK, queue depth: ${Math.floor(rng(0, 12))}`)
      if (t >= cfg.duration) {
        clearInterval(demoRef.current)
        setRunning(false)
        setStatus(`simulation complete — ${t}s captured`)
        addLog('ok', '> Simulation complete.')
      }
    }, 1000)
  }

  useEffect(() => { return () => clearInterval(demoRef.current) }, [])

  // ── Mininet command preview ──────────────────────────────────────────────
  const mnCmd = `mn --topo ${topoType},${nSwitches} --link tc,bw=${bw},delay=${delay}ms,jitter=${jitter}ms --bottleneck-bw=${bnBw} --loss=${pktLoss}`

  const logColor = { ok: '#3fb950', warn: '#d29922', info: '#7eb6ff', error: '#f85149' }

  return (
    <div style={s.app}>

      {/* HEADER */}
      <div style={s.header}>
        <span style={{ fontFamily: "'Syne',sans-serif", fontSize: 18, fontWeight: 600, letterSpacing: '-0.02em' }}>
          CGReplay++
        </span>
        <span style={{ ...s.badge, background: 'rgba(56,139,253,0.12)', color: '#7eb6ff', border: '0.5px solid rgba(56,139,253,0.3)' }}>
          Cloud Gaming Emulator
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6,
          fontFamily: "'JetBrains Mono',monospace", fontSize: 11,
          color: wsConn ? '#3fb950' : '#484f58' }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%',
            background: wsConn ? '#3fb950' : '#484f58',
            boxShadow: wsConn ? '0 0 6px #3fb950' : 'none' }} />
          {wsConn ? 'backend connected' : 'demo mode'}
        </span>
      </div>

      {/* TOPOLOGY */}
      <div style={s.panel}>
        <div style={s.panelHead}>
          <i className="ti ti-topology-star-3" style={{ fontSize: 15, color: '#388bfd' }} aria-hidden="true" />
          <span style={s.panelTitle}>mininet topology</span>
          <span style={{ ...s.badge, background: 'rgba(56,139,253,0.1)', color: '#7eb6ff' }}>live preview</span>
        </div>
        <div style={s.body}>
          <TopologyViz switches={nSwitches} bw={bw} delay={delay} bnBw={bnBw} loss={pktLoss} />
          <div style={s.grid3}>
            <div style={s.field}>
              <label style={s.label}># Switches (path)</label>
              <div style={s.fieldRow}>
                <input type="number" value={nSwitches} min={1} max={8}
                  style={{ width: 60 }}
                  onChange={e => setNSwitches(parseInt(e.target.value) || 1)} />
                <span style={s.unit}>H0 ↔ S1…Sn ↔ H1</span>
              </div>
            </div>
            <div style={s.field}>
              <label style={s.label}># Devices</label>
              <input type="number" value={nDevices} min={2} max={16}
                onChange={e => setNDevices(parseInt(e.target.value) || 2)} />
            </div>
            <div style={s.field}>
              <label style={s.label}>Topology type</label>
              <select value={topoType} onChange={e => setTopoType(e.target.value)}>
                {TOPO_OPTS.map(o => <option key={o}>{o}</option>)}
              </select>
            </div>
          </div>
        </div>
      </div>

      {/* NETWORK CONDITIONS */}
      <div style={s.panel}>
        <div style={s.panelHead}>
          <i className="ti ti-wave-sine" style={{ fontSize: 15, color: '#388bfd' }} aria-hidden="true" />
          <span style={s.panelTitle}>network conditions</span>
        </div>
        <div style={s.body}>
          <div style={{ ...s.grid3, marginBottom: 12 }}>
            <div style={s.field}>
              <label style={s.label}>Delay (all links)</label>
              <div style={s.fieldRow}>
                <input type="number" value={delay} min={0} max={500}
                  onChange={e => setDelay(parseFloat(e.target.value) || 0)} />
                <span style={s.unit}>ms</span>
              </div>
            </div>
            <div style={s.field}>
              <label style={s.label}>Jitter (all links)</label>
              <div style={s.fieldRow}>
                <input type="number" value={jitter} min={0} max={100} step={0.1}
                  onChange={e => setJitter(parseFloat(e.target.value) || 0)} />
                <span style={s.unit}>ms</span>
              </div>
            </div>
            <div style={s.field}>
              <label style={s.label}>Bandwidth (all links)</label>
              <div style={s.fieldRow}>
                <input type="number" value={bw} min={1} max={10000}
                  onChange={e => setBw(parseFloat(e.target.value) || 1)} />
                <span style={s.unit}>Mbps</span>
              </div>
            </div>
          </div>
          <div style={s.divider}>
            <span style={s.dividerText}>bottleneck — last link only</span>
            <div style={s.dividerLine} />
          </div>
          <div style={s.grid2}>
            <div style={s.field}>
              <label style={s.label}>Pkt loss (bottleneck)</label>
              <div style={s.fieldRow}>
                <input type="number" value={pktLoss} min={0} max={50} step={0.1}
                  onChange={e => setPktLoss(parseFloat(e.target.value) || 0)} />
                <span style={s.unit}>%</span>
              </div>
            </div>
            <div style={s.field}>
              <label style={s.label}>Bottleneck bandwidth</label>
              <div style={s.fieldRow}>
                <input type="number" value={bnBw} min={0.1} max={1000} step={0.1}
                  onChange={e => setBnBw(parseFloat(e.target.value) || 0.1)} />
                <span style={s.unit}>Mbps</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* CODEC & TRANSPORT */}
      <div style={s.panel}>
        <div style={s.panelHead}>
          <i className="ti ti-device-tv" style={{ fontSize: 15, color: '#388bfd' }} aria-hidden="true" />
          <span style={s.panelTitle}>codec &amp; transport configuration</span>
        </div>
        <div style={s.body}>
          <div style={{ ...s.grid3, marginBottom: 12, gap: 14 }}>
            <div>
              <div style={s.divider}><span style={s.dividerText}>codec</span><div style={s.dividerLine} /></div>
              <ChipGroup options={CODECS} value={codec} onChange={setCodec} color="blue" />
            </div>
            <div>
              <div style={s.divider}><span style={s.dividerText}>transport protocol</span><div style={s.dividerLine} /></div>
              <ChipGroup options={PROTOCOLS} value={proto} onChange={setProto} color="green" />
            </div>
            <div>
              <div style={s.divider}><span style={s.dividerText}>game</span><div style={s.dividerLine} /></div>
              <ChipGroup options={GAMES} value={game} onChange={setGame} color="amber" />
            </div>
          </div>
          <div style={{ ...s.grid2, gap: 12, marginBottom: 12 }}>
            <div>
              <div style={s.divider}><span style={s.dividerText}>frame rate</span><div style={s.dividerLine} /></div>
              <ChipGroup options={FPS_OPTS} value={fps} onChange={setFps} color="blue" />
            </div>
            <div>
              <div style={s.divider}><span style={s.dividerText}>resolution</span><div style={s.dividerLine} /></div>
              <ChipGroup options={RES_OPTS} value={res} onChange={setRes} color="blue" />
            </div>
          </div>
          <div style={s.grid2}>
            <div style={s.field}>
              <label style={s.label}>Simulation duration</label>
              <div style={s.fieldRow}>
                <input type="number" value={duration} min={5} max={300}
                  onChange={e => setDuration(parseInt(e.target.value) || 30)} />
                <span style={s.unit}>seconds</span>
              </div>
            </div>
            <div style={s.field}>
              <label style={s.label}>Config file path</label>
              <input type="text" value={cfgPath} onChange={e => setCfgPath(e.target.value)} />
            </div>
          </div>
        </div>
      </div>

      {/* RUN CONTROL */}
      <div style={s.panel}>
        <div style={s.panelHead}>
          <i className="ti ti-terminal" style={{ fontSize: 15, color: '#3fb950' }} aria-hidden="true" />
          <span style={s.panelTitle}>simulation control</span>
        </div>
        <div style={s.body}>
          <div style={{ ...s.logWin, marginBottom: 10, minHeight: 32 }}>
            <span style={{ color: '#7eb6ff' }}>{mnCmd}</span>
          </div>
          <button onClick={handleRun} style={{
            width: '100%', height: 38, borderRadius: 8, border: 'none',
            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            fontFamily: "'JetBrains Mono',monospace", fontSize: 12, fontWeight: 500,
            background: running ? '#7d1b1b' : '#1a3a1a',
            color: running ? '#f85149' : '#3fb950',
            border: `0.5px solid ${running ? '#f85149' : '#3fb950'}`,
            transition: 'all 0.15s',
          }}>
            <i className={`ti ${running ? 'ti-player-stop' : 'ti-player-play'}`} aria-hidden="true" />
            {running ? 'stop simulation' : 'start simulation'}
          </button>
        </div>
        <div style={s.statusBar}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
            background: running ? '#3fb950' : '#484f58',
            boxShadow: running ? '0 0 6px #3fb950' : 'none',
            animation: running ? 'none' : 'none',
          }} />
          <span>{status}</span>
          <span style={{ marginLeft: 'auto', color: '#484f58' }}>
            {elapsed}s / {duration}s
          </span>
        </div>
      </div>

      {/* QoE METRICS */}
      <div style={s.panel}>
        <div style={s.panelHead}>
          <i className="ti ti-chart-line" style={{ fontSize: 15, color: '#388bfd' }} aria-hidden="true" />
          <span style={s.panelTitle}>perceived quality — QoE metrics</span>
          <span style={{ ...s.badge, background: 'rgba(56,139,253,0.1)', color: '#7eb6ff' }}>real-time</span>
        </div>
        <div style={s.body}>
          <div style={{ ...s.grid4, marginBottom: 12 }}>
            {[
              { key: 'rt',   label: 'Response Time', unit: 'ms',  color: '#f85149', val: metrics.rt   },
              { key: 'vmaf', label: 'VMAF',           unit: '',    color: '#388bfd', val: metrics.vmaf },
              { key: 'ssim', label: 'SSIM',           unit: '',    color: '#3fb950', val: metrics.ssim },
              { key: 'psnr', label: 'PSNR',           unit: 'dB',  color: '#d29922', val: metrics.psnr },
            ].map(m => (
              <div key={m.key} style={s.metricCard}>
                <div style={s.metricLabel}>{m.label}</div>
                <div style={{ ...s.metricValue, color: running ? m.color : '#484f58' }}>
                  {m.val !== null ? m.val : '—'}
                  <span style={{ fontSize: 11, color: '#8b949e' }}>{m.val !== null ? m.unit : ''}</span>
                </div>
              </div>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {[
              { title: 'response time (ms) vs time', data: rtData,   color: '#f85149', unit: 'ms', yDomain: [0, 'auto'] },
              { title: 'VMAF score vs time',          data: vmafData, color: '#388bfd', unit: '',   yDomain: [0, 100] },
              { title: 'SSIM vs time',                data: ssimData, color: '#3fb950', unit: '',   yDomain: [0, 1] },
              { title: 'PSNR (dB) vs time',           data: psnrData, color: '#d29922', unit: 'dB', yDomain: [20, 50] },
            ].map((c, i) => (
              <div key={i} style={s.chartCard}>
                <div style={s.chartTitle}>{c.title}</div>
                <ResponsiveContainer width="100%" height={130}>
                  <LineChart data={c.data} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                    <CartesianGrid stroke="rgba(255,255,255,0.04)" />
                    <XAxis dataKey="t" tick={{ fontSize: 9, fill: '#484f58', fontFamily: 'JetBrains Mono,monospace' }} />
                    <YAxis domain={c.yDomain} tick={{ fontSize: 9, fill: '#484f58', fontFamily: 'JetBrains Mono,monospace' }} />
                    <Tooltip content={<ChartTooltip unit={c.unit} />} />
                    <Line type="monotone" dataKey="v" stroke={c.color}
                      strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* GAME SESSION */}
      <div style={s.panel}>
        <div style={s.panelHead}>
          <i className="ti ti-device-gamepad-2" style={{ fontSize: 15, color: '#d29922' }} aria-hidden="true" />
          <span style={s.panelTitle}>cloud gaming session</span>
        </div>
        <div style={s.body}>
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 12, alignItems: 'start' }}>
            <div>
              <div style={{ borderRadius: 8, overflow: 'hidden', border: '0.5px solid rgba(255,255,255,0.08)', position: 'relative' }}>
                {!running && (
                  <div style={{
                    position: 'absolute', inset: 0, zIndex: 2, display: 'flex',
                    alignItems: 'center', justifyContent: 'center',
                    background: 'rgba(5,8,16,0.85)', flexDirection: 'column', gap: 8,
                    fontFamily: "'JetBrains Mono',monospace", fontSize: 12, color: 'rgba(255,255,255,0.25)',
                    textAlign: 'center',
                  }}>
                    <i className="ti ti-device-gamepad-2" style={{ fontSize: 36, display: 'block', opacity: 0.2 }} aria-hidden="true" />
                    start simulation to view game stream
                  </div>
                )}
                <GameCanvas running={running} codec={codec} proto={proto} fps={fps} loss={pktLoss} />
                <LiveFrame running={running} />
              </div>
            </div>
            <div>
              <div style={s.divider}><span style={s.dividerText}>command log</span><div style={s.dividerLine} /></div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3, maxHeight: 160, overflowY: 'auto', marginBottom: 10 }}>
                {commands.length === 0
                  ? <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: '#484f58', textAlign: 'center', padding: '8px 0' }}>no commands yet</div>
                  : commands.map((c, i) => (
                    <div key={i} style={{
                      display: 'flex', alignItems: 'center', gap: 8, padding: '3px 8px',
                      borderRadius: 5, background: 'rgba(255,255,255,0.02)',
                      border: '0.5px solid rgba(255,255,255,0.06)',
                      fontFamily: "'JetBrains Mono',monospace", fontSize: 11,
                    }}>
                      <span style={{
                        fontSize: 9, padding: '1px 5px', borderRadius: 100, fontWeight: 500,
                        background: c.dir === 'UP' ? 'rgba(56,139,253,0.15)' : 'rgba(63,185,80,0.12)',
                        color: c.dir === 'UP' ? '#7eb6ff' : '#56d364',
                      }}>{c.dir}</span>
                      <span style={{ color: '#484f58', minWidth: 36 }}>{c.time}s</span>
                      <span style={{ flex: 1, color: c.dropped ? '#f85149' : '#e6edf3' }}>
                        {c.cmd}{c.dropped ? ' ✗' : ''}
                      </span>
                      <span style={{ color: '#d29922' }}>{c.latency}ms</span>
                    </div>
                  ))
                }
              </div>
              <div style={s.divider}><span style={s.dividerText}>mininet log</span><div style={s.dividerLine} /></div>
              <div style={s.logWin} ref={logRef}>
                {logs.map((l, i) => (
                  <div key={i} style={{ lineHeight: 1.7, color: logColor[l.level] || '#7eb6ff' }}>{l.msg}</div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div style={{ textAlign: 'center', fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: '#484f58', padding: '8px 0' }}>
        CGReplay++ — UFSCar LERIS Lab &nbsp;·&nbsp; github.com/dcomp-leris/CGReplaypp
      </div>
    </div>
  )
}
