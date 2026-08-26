# Decision Desk Page Override

## Purpose

重复查看、快速确认和审计一次决策运行。页面是操作台，不是营销首页、监控大屏或 JSON 浏览器。

## Visual Direction

- Canvas: `#f4f6f8`; surfaces: `#ffffff`; ink: `#172033`; muted: `#748096`.
- Navigation: deep navy `#152238`; primary action: restrained blue `#214a77`.
- Status colors: green for publish/healthy, amber for degraded/needs review, red for reject, neutral for research-only.
- Use DM Sans for UI copy and IBM Plex Mono for IDs, timestamps, versions and quantitative values.
- Use compact 8px-radius panels, 1px borders, no nested cards, no purple/pink gradients, no decorative blobs, no oversized hero.

## Layout

- Persistent left navigation; collapses to icon rail at tablet widths.
- Top bar contains breadcrumb, search, notification and owner identity.
- Inbox first viewport: four operational metrics, recent event table, health panel, forecast coverage and asset summary.
- Run detail opens as a right drawer and keeps the table context visible.
- Raw provider/model payload is never on the primary page; technical detail is a deliberate secondary action.

## Interaction And Accessibility

- Keyboard-visible focus rings, tooltip labels for icon-only actions, `aria-label` on search/close/collapse controls.
- Running state refreshes through Query polling; terminal state stops polling.
- Loading/error/empty states must be explicit and recoverable.
- Respect `prefers-reduced-motion`; row hover never changes layout dimensions.
- Verify at 375px, 768px, 1024px and 1440px before visual sign-off.
