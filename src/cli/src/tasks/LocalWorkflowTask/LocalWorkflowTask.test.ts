import { expect, test } from 'bun:test'
import { skipWorkflowAgent, killWorkflowTask } from './LocalWorkflowTask.js'

function fakeState(taskId: string, controllers: Map<string, AbortController>) {
  return {
    tasks: {
      [taskId]: {
        id: taskId, type: 'local_workflow', status: 'running',
        description: 'wf', startTime: Date.now(), outputFile: '', outputOffset: 0,
        notified: false, agentCount: 1, agentControllers: controllers,
      },
    },
  }
}

test('skip aborts the named agent controller', () => {
  const ctrl = new AbortController()
  const controllers = new Map([['a1', ctrl]])
  let state: any = fakeState('w1', controllers)
  const setAppState = (f: any) => { state = f(state) }
  skipWorkflowAgent('w1', 'a1', setAppState)
  expect(ctrl.signal.aborted).toBe(true)
})

test('kill aborts the run controller and marks killed', () => {
  const runCtrl = new AbortController()
  const controllers = new Map<string, AbortController>()
  let state: any = fakeState('w2', controllers)
  state.tasks['w2'].runController = runCtrl
  const setAppState = (f: any) => { state = f(state) }
  killWorkflowTask('w2', setAppState)
  expect(runCtrl.signal.aborted).toBe(true)
  expect(state.tasks['w2'].status).toBe('killed')
})
