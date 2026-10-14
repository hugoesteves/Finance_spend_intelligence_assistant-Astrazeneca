import { useState } from 'react'
import { Chat, type Turn } from './Chat'
import { Compliance } from './Compliance'

export default function App() {
  const [tab, setTab] = useState<'chat' | 'compliance'>('chat')
  const [turns, setTurns] = useState<Turn[]>([]) // kept here so the chat survives a tab switch

  return (
    <div className="app">
      <header>
        <h1>Finance spend assistant</h1>
        <nav>
          <button className={tab === 'chat' ? 'active' : ''} onClick={() => setTab('chat')}>
            Talk with Data
          </button>
          <button className={tab === 'compliance' ? 'active' : ''} onClick={() => setTab('compliance')}>
            Data Quality Report
          </button>
        </nav>
      </header>
      <main>{tab === 'chat' ? <Chat turns={turns} setTurns={setTurns} /> : <Compliance />}</main>
    </div>
  )
}
