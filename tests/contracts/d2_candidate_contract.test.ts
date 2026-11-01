import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import * as contracts from '../../packages/contracts_ts/src/generated/r2'

const corpus = JSON.parse(readFileSync(new URL('../../artifacts/d2-candidate-contract/wire-corpus.json', import.meta.url), 'utf8'))

describe('canonical D2 Python/TypeScript wire acceptance', () => {
  for (const entry of corpus) {
    it(entry.name, () => {
      const result = (contracts as Record<string, any>)[entry.typescript].safeParse(entry.value)
      expect(result.success).toBe(entry.valid)
      if (entry.valid) expect(result.data).toEqual(entry.value)
    })
  }
})
