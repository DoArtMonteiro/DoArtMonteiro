# Portfolio design system

## Direction

**Night Atlas × Provenance Archive** pairs a cinematic technical identity with clear, evidence-led project records. The visual layer gives the profile character; the writing stays direct and verifiable.

The supporting line is **“Systems for work. Space for play.”** The professional position remains **Technology × Business × Operations**.

## Visual language

- Dark theme: graphite navy, restrained cyan and small amber markers.
- Light theme: warm paper, deep teal and the same amber markers.
- Geometry: grids, paths and nodes that suggest systems and evidence. Avoid interface mock-ups, statistics without provenance, decorative badges and animation.
- Typography: GitHub's own text rendering for the README; Arial or a system sans-serif inside self-contained SVG assets.
- Layout: one-column reading order in Markdown. Headings, links and text remain meaningful without images.

## Information order

1. Identity, position and a short introduction.
2. Four engineering projects with equal heading level, current status, contribution and evidence.
3. Creative projects and demonstrations, including their relevant limitations.
4. GitHub-native activity and a link to the data-provenance guide.

The README uses semantic headings and a theme-aware `<picture>` for the banner. GitHub documents this pattern for light and dark profile images. It does not rely on custom CSS, JavaScript, remote widget services or a fixed-width card grid.

## Asset rules

- Architecture diagrams explain code or product flows; they are not application screenshots.
- A product screenshot must come from the real application, include a source revision, and be refreshed only after a material UI change.
- Generated activity graphics must carry a source-through date and provenance. No custom activity graphic is shown as current until a complete public-only refresh has passed review.
- The same source values generate light, dark, 2D and 3D calendar variants. The accessible summary and CSV remain available independently of the images.
- Screenshots and diagrams have meaningful alternative text and a text explanation in the README.

## Current scope

The visual assets in this branch are a banner, simplified engineering diagrams and one genuine OmniQuiz screenshot linked to an immutable source commit. Alibi and Atlas Arcade remain linked to their live demonstrations; no invented UI captures are used. The custom live calendar and activity dashboard are not yet published.
