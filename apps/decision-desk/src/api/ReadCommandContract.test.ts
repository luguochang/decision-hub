import { readCommandReceiptSchema } from '@decision-hub/contracts-ts'
import { expect, it } from 'vitest'

function receipt() {
  return {
    schema_version: 'read-command-receipt.v1', request_id: 'original-command',
    revision_id: 'original-revision', owner_id: '原用户',
    command: { request_id: 'original-command', read: true },
    result: { revision_id: 'original-revision', owner_id: '原用户', read_at: '2026-10-03T04:00:00.123456Z', updated_at: '2026-10-03T04:00:01.234567Z' },
    payload_hash: 'a'.repeat(64), confirmation_basis: 'stored_command_hash',
  }
}

it('preserves the original microsecond wire strings and Unicode identity', () => {
  expect(readCommandReceiptSchema.parse(receipt())).toEqual(receipt())
})

it.each(['schema_version', 'confirmation_basis'])('requires explicit %s', missing => {
  const value: Record<string, unknown> = receipt()
  delete value[missing]
  expect(readCommandReceiptSchema.safeParse(value).success).toBe(false)
})

it('refuses scalar coercion in original commands and result timestamps', () => {
  expect(readCommandReceiptSchema.safeParse({ ...receipt(), command: { request_id: 'original-command', read: 1 } }).success).toBe(false)
  expect(readCommandReceiptSchema.safeParse({ ...receipt(), result: { ...receipt().result, updated_at: 1760000000 } }).success).toBe(false)
})

it('counts Unicode identifiers by code points at the canonical boundary', () => {
  const value = receipt()
  value.owner_id = value.result.owner_id = '😀'.repeat(128)
  expect(readCommandReceiptSchema.safeParse(value).success).toBe(true)
  value.owner_id += '😀'
  expect(readCommandReceiptSchema.safeParse(value).success).toBe(false)
})

it.each(['a'.repeat(63), 'a'.repeat(65), 'A'.repeat(64), 'a'.repeat(64) + '\n'])('requires an exact lowercase hash length', payload_hash => {
  expect(readCommandReceiptSchema.safeParse({ ...receipt(), payload_hash }).success).toBe(false)
})
