import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import Home from './pages/Home'
import VideoDetail from './pages/VideoDetail'
import Compare from './pages/Compare'
import HealthBanner from './components/HealthBanner'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 5000 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="app-shell">
          <div className="topbar">
            <Link to="/" className="brand">
              <span className="brand-mark">FS</span>
              FlowScope
            </Link>
            <nav className="nav-links">
              <Link to="/">Analyses</Link>
            </nav>
          </div>
          <HealthBanner />
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/videos/:videoId" element={<VideoDetail />} />
            <Route path="/compare" element={<Compare />} />
          </Routes>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
