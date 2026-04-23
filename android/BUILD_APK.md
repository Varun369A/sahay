# Sahay — Building the APK

This file is a placeholder for the exact APK build steps **you** want to use
(your keystore strategy, signing config, distribution channel).

The Gradle config is already set up to accept these as `-P` properties —
you only need to fill in your specifics:

```bash
./gradlew assembleRelease \
    -PSAHAY_API_BASE=https://api.sahay.app \
    -PSAHAY_SARVAM_KEY=$SARVAM_API_KEY \
    -PSAHAY_KEYSTORE=/path/to/sahay.jks \
    -PSAHAY_KEYSTORE_PASSWORD=$KEYSTORE_PWD \
    -PSAHAY_KEY_ALIAS=sahay \
    -PSAHAY_KEY_PASSWORD=$KEY_PWD
```

Output:
- `app/build/outputs/apk/release/app-release.apk` — sideload-ready
- `app/build/outputs/bundle/release/app-release.aab` — Play Store

## What you need to tell me before we finalise the APK build

1. **Keystore strategy** — generate fresh, or use an existing one?
2. **Distribution** — Play Store, sideload-direct only, or both?
3. **Versioning** — manual `versionCode` bump per build, or git-tag-driven?
4. **Signing service** — local, GitHub Actions secrets, Play App Signing?
5. **Build environment** — your local machine, or CI runner?
6. **API base URL** — the production host (e.g. `https://api.sahay.app`)?

Once you answer, I'll add a final Gradle task and CI workflow that produces
a signed APK end-to-end.
