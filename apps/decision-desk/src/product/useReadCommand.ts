import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { checkedReadCommandReceipt, checkedRevisionReadReceipt, delivery, productOwner, type RevisionReadRequest } from '../api/delivery'
import { acknowledgeReadCommand, assertPendingReadCommand, beginReadCommand, persistReadCommand, readCommandState, ReadCommandJournalError, subscribeReadCommands } from '../research/readCommandRequests'

export function useReadCommand() {
  const client = useQueryClient()
  const [journal, setJournal] = useState(readCommandState)
  const [operationError, setOperationError] = useState<string | null>(null)
  const [storageBlocked, setStorageBlocked] = useState(false)
  const [confirmed, setConfirmed] = useState<RevisionReadRequest | null>(null)
  useEffect(() => subscribeReadCommands(() => setJournal(readCommandState())), [])

  async function getReceipt(value: RevisionReadRequest) {
    assertPendingReadCommand(value)
    // Check again at the UI boundary, including when the API method is replaced
    // by a controlled transport in tests. A list projection is never an ACK.
    return checkedReadCommandReceipt(await delivery.readCommandReceipt(value), value)
  }
  async function dispatch(value: RevisionReadRequest) {
    assertPendingReadCommand(value)
    const weak = await delivery.read(value.revision, value.read, value.id)
    checkedRevisionReadReceipt(weak, value)
    return getReceipt(value)
  }
  async function run(value: RevisionReadRequest, first: boolean) {
    let release: (() => void) | undefined
    try {
      release = beginReadCommand()
      setOperationError(null); setConfirmed(null)
      if (first) persistReadCommand(value)
      else assertPendingReadCommand(value)
      const existing = first ? null : await getReceipt(value)
      const receipt = existing ?? await dispatch(value)
      if (receipt === null) throw new Error('原阅读请求尚无可核对回执，标记未确认。请显式重试原请求。')
      acknowledgeReadCommand(value)
      setConfirmed(value)
      setStorageBlocked(false)
      // The acknowledged command may precede a later opposite command. Always
      // re-read the current list rather than copying its historical read_at.
      await client.invalidateQueries({ queryKey: ['product-reports'] })
    } catch (error) {
      if (error instanceof ReadCommandJournalError) setStorageBlocked(true)
      setOperationError(error instanceof Error ? error.message : '阅读标记尚未确认，请核对原请求回执。')
    } finally {
      release?.()
      setJournal(readCommandState())
    }
  }
  function mark(revision: string, read: boolean) {
    const current = readCommandState()
    setJournal(current)
    if (current.busy || current.pending || current.error || storageBlocked) return
    try {
      const value: RevisionReadRequest = { revision, read, owner: productOwner, id: `report-read:${crypto.randomUUID()}` }
      void run(value, true)
    } catch {
      setOperationError('无法生成阅读请求凭据，尚未发送请求。请保留当前页面并稍后重试。')
    }
  }
  function retry() {
    const current = readCommandState()
    setJournal(current)
    if (current.busy || current.error || !current.pending) return
    void run(current.pending, false)
  }
  function restore() {
    setJournal(readCommandState()); setOperationError(null); setStorageBlocked(false)
  }
  return {
    pending: journal.pending, busy: journal.busy, confirmed,
    error: journal.error ?? operationError,
    blocked: journal.busy || Boolean(journal.pending || journal.error) || storageBlocked,
    storageBlocked: Boolean(journal.error) || storageBlocked, mark, retry, restore,
  }
}
