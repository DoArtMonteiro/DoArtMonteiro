# Portfolio design system

## Direction

**Night Atlas × Provenance Archive** pairs a cinematic technical identity with clear, evidence-led project records. The visual layer gives the profile character; the writing stays direct and verifiable.

The supporting line is **“Systems for work. Space for play.”** The professional position remains **Technology × Business × Operations**.

## Visual language

- Dark theme: graphite navy, restrained cyan and small amber markers.
- Light theme: warm paper, deep teal and the same amber markers.
- Geometry: grids, paths and nodes that suggest systems and evidence. Avoid interface mock-ups, statistics without provenance, and decorative badges.
- Motion: use only brief, one-shot GIFs that reveal a real data visual or trace a path through the hero. Never loop. A static SVG remains the fallback for reduced motion, and a linked text or 2D alternative stays available. Motion communicates the same information as the static asset; it does not imply live data or interactivity.
- Typography: GitHub's own text rendering for the README; Arial or a system sans-serif inside self-contained SVG assets.
- Layout: one-column reading order in Markdown. Headings, links and text remain meaningful without images.

## Information order

1. Identity, position and a short introduction.
2. Four engineering projects with equal heading level, current status, contribution and evidence.
3. Creative projects and demonstrations, including their relevant limitations.
4. GitHub-native activity and a link to the data-provenance guide.

The README uses semantic headings and theme-aware `<picture>` elements for its banner and generated activity figures. Narrow-screen dashboard sources precede the desktop variants. Where GitHub accepts the media query, motion is selected only for `prefers-reduced-motion: no-preference`; the `<img>` fallback remains static. The page does not rely on custom CSS, JavaScript, remote widget services or layout-dependent HTML cards.

## Asset rules

- Architecture diagrams explain code or product flows; they are not application screenshots.
- A product screenshot must come from the real application, include a source revision, and be refreshed only after a material UI change.
- Generated activity graphics must carry a source-through date and provenance. No custom activity graphic is shown as current until a complete public-only refresh has passed review.
- The same source values generate light, dark, 2D and 3D calendar variants. The accessible summary and CSV remain available independently of the images.
- The project activity dashboard has desktop and narrow-screen layouts. Both use the same validated snapshot and provide light and dark versions; the linked report contains repository links and the full timeline.
- Screenshots and diagrams have meaningful alternative text and a text explanation in the README.
- Automatic data refresh, GIF animation and interactive demos are separate: a successful refresh changes source data; a finite GIF only presents one saved snapshot; interactive behaviour is available through links to the real applications.

## Current scope

The draft branch includes a dated, public-only calendar and desktop/mobile activity dashboards alongside the banner, simplified engineering diagrams and one genuine OmniQuiz screenshot linked to an immutable source commit. The snapshot is embedded in the README on `portfolio/v2.2-living`; it is not yet integrated into profile `main`, refreshed automatically or presented as live data. Alibi and Atlas Arcade remain linked to their live demonstrations; no invented UI captures are used.
