# Design system and page reference

Source of the look: the structure and styling cues of the Firebase website (firebase.google.com): dark hero with a warm orange glow, Google-style blue pill buttons, rounded bordered cards, generous spacing, short benefit-led headings, a "How it works" flow, and a simple footer with Terms and Privacy. Exact values below are approximations; adjust them in `theme.py` only (CSS variables at the top).

## Tokens

| Token | Value | Use |
|---|---|---|
| Primary blue | `#1a73e8` (hover `#1967d2`) | Primary buttons, links, focus ring |
| Light blue | `#e8f0fe`, `#a8c7fa` | Icon tiles, info banners, hover states |
| Firebase yellow / amber / orange | `#ffca28`, `#ffa000`, `#f57c00` | Brand mark, hero highlight, step numbers |
| Flame | `#dd2c00` | Brand mark gradient end only |
| Text / secondary text | `#202124` / `#5f6368` | Body / helper text |
| Border | `#dadce0` | Cards, inputs, dividers |
| Surface / alt surface | `#ffffff` / `#f8f9fa` | Page / sidebar and bands |
| Hero background | `#131314` + orange radial glow | Landing hero only |
| Error / warn / ok | `#d93025` / `#fef7e0` / `#188038` | Banners |
| Radius | cards 16px, inputs 8px, buttons pill, hero 28px | |
| Type | Google Sans if available, else Roboto, then system sans | Headings weight 500 |
| Icons | Material Symbols Rounded | Feature cards, nav |

## Landing page copy guide

- Headline (benefit first): "Ask questions about your company documents and data, <accent>with answers you can trust</accent>."
- Subtitle: one sentence on approved documents, role-based access, and cited answers.
- Feature cards: "Answers with sources", "Access that follows your role", "HR data, protected".
- Steps: 1 Sign in with your employee ID. 2 Ask a question in plain language. 3 Get an answer with citations.
- Trust band bullets: no self-registration; you only see what your role allows; every answer cites its sources; HR data is available to authorized staff only.
- Never include invented numbers, customer logos, quotes, or compliance badges.

## Chat page details

- Main column narrow (about 860px) for readability.
- User messages plain; assistant messages on the alt-surface rounded block.
- Sources: small chips (`chips()`), one per citation, label = document title plus section, or "HR CSV".
- Suggestion buttons (empty state) should be generic questions the user can actually ask; do not hardcode answers.
- Sidebar chat titles: first 40 characters of the first question.

## Privacy page outline (describe only what is true; mark unknowns `[TO CONFIRM]`)

1. Summary and "demonstration project" note
2. Information the app handles: employee ID, name, access groups, questions asked, answers and citations, session information
3. Information it does not keep: passwords in plain text (only hashes are stored for login)
4. How information is used: authenticating you, applying access rules, answering questions
5. Documents and HR data: answers come only from content your role may access; HR data is limited to authorized roles
6. Third-party processing: questions and retrieved text are sent to the configured language-model provider `[TO CONFIRM provider and terms]`
7. Storage and retention of chat history `[TO CONFIRM]`
8. Security measures (token-based sign-in, role-based access) without absolute guarantees
9. Your choices and how to contact the administrator `[TO CONFIRM contact]`
10. Changes to this policy, last updated date

## Terms page outline

1. Acceptance and demonstration-project note
2. Who may use the service: provisioned employees only; no sharing credentials
3. Acceptable use: lawful, work-related use; no attempts to bypass access controls
4. AI-generated answers may be incomplete or wrong; verify using the cited sources
5. Confidential information: do not copy or share restricted content
6. Account suspension for misuse
7. Availability and changes
8. Limitation of responsibility `[TO CONFIRM with an owner or legal reviewer]`
9. Contact `[TO CONFIRM]`, last updated date

Both legal pages end with a note that the text is a project template and must be reviewed by the responsible owner before real use.

## Page and file conventions

- File names: `landing.py`, `login.py`, `chat.py`, `privacy.py`, `terms.py`.
- Each page: `apply_theme()`, then nav, then content, then `footer()` plus Privacy/Terms `st.page_link`s on public pages.
- Session keys (single place, constants): `auth_token`, `display_name`, `access_groups`, `chats`, `active_chat_id`, `is_busy`.