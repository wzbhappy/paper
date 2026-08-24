import { Route, Routes } from 'react-router-dom'
import Dashboard from './features/dashboard/Dashboard'
import ProjectDetail from './features/project/ProjectDetail'

export default function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto max-w-5xl px-6 py-4">
          <h1 className="text-lg font-bold text-gray-900">Paper Assistant</h1>
          <p className="text-xs text-gray-500">论文撰写全流程辅助平台</p>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
        </Routes>
      </main>
    </div>
  )
}
