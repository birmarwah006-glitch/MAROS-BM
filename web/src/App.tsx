import { Suspense, lazy } from 'react'
import { Routes, Route, Navigate, Outlet } from 'react-router-dom'
import { AppShell } from '@/components/AppShell'
import { Container, Skeleton } from '@/components/ui'
import Landing from '@/pages/Landing'

// Route-level code splitting. The landing page and Explore are the entry
// points, so Landing stays in the main chunk; the heavier learning surfaces
// (markdown, KaTeX, and the on-demand mermaid load) are only fetched when a
// student actually opens one.
const Feed = lazy(() => import('@/pages/Feed'))
const Explore = lazy(() => import('@/pages/Explore'))
const Lecture = lazy(() => import('@/pages/Lecture'))
const Lesson = lazy(() => import('@/pages/Lesson'))
const AddLecture = lazy(() => import('@/pages/AddLecture'))
const Processing = lazy(() => import('@/pages/Processing'))
const MyLearning = lazy(() => import('@/pages/MyLearning'))
const Ask = lazy(() => import('@/pages/Ask'))
const Profile = lazy(() => import('@/pages/Profile'))

function RouteFallback() {
  return (
    <Container>
      <div className="space-y-4 py-14">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-48 w-full" />
      </div>
    </Container>
  )
}

export default function App() {
  return (
    <Routes>
      {/* The feed is full-bleed and owns the whole viewport, so it sits
          OUTSIDE the app shell — no top bar, no bottom tabs, no footer. */}
      <Route
        path="/feed"
        element={
          <Suspense fallback={<div className="h-[100dvh] bg-black" />}>
            <Feed />
          </Suspense>
        }
      />

      <Route element={<AppShell />}>
        <Route path="/" element={<Landing />} />
        <Route
          element={
            <Suspense fallback={<RouteFallback />}>
              <Outlet />
            </Suspense>
          }
        >
          <Route path="/explore" element={<Explore />} />
          <Route path="/lecture/:jobId" element={<Lecture />} />
          <Route path="/lecture/:jobId/module/:moduleId" element={<Lesson />} />
          <Route path="/add" element={<AddLecture />} />
          <Route path="/processing/:jobId" element={<Processing />} />
          <Route path="/learning" element={<MyLearning />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="/profile" element={<Profile />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
