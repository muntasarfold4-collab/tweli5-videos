# Faces library

Photos of famous people that the user sent in chat for Actual News shows. Runs reuse them automatically
when the same person is in a story, so the user is only asked about new faces.

- Only photos the user supplied. Never add photos found on the web.
- File name: `<slug>.jpg` (e.g. `ishowspeed.jpg`), landscape, face roughly centred.
- Register every file in `index.json` (`name`, `aliases`, `file`, `focus`, `credit`, `added`).
- A newer photo from the user replaces the old one for that person.
