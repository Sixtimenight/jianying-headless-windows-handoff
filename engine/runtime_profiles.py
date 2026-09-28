"""Reviewed application identities; never infer compatibility from a version prefix."""
PRIMARY_VERSION = '11.5.0'
PROFILE_PREFIX = 'jy14-headless-macos-'
WINDOWS_PROFILE_PREFIX = 'jy14-headless-windows-'
PROFILES = {
    '11.5.0': '2041482a1aaeffa4d8bd69b836f8cf38807aaad8021bca410d567c59af3bccfa',
    '11.4.2': '632c8ddd09ff4a54f876cd8142eb505055ee26d944199506b230949b7e106bd1',
    '11.4.0': 'a1693070036a6678bb5db35f71d2105812ad24a2370e7e91c78712cc0d6455f3',
}
EXPORT_PROFILES = frozenset([PROFILE_PREFIX + v for v in ('11.5.0', '11.4.2')] +
                            [WINDOWS_PROFILE_PREFIX + '11.5.0.14471'])
RESOURCE_CAPTURE_PROFILE = PROFILE_PREFIX + '11.4.2'
TIMELINE_SCHEMAS = frozenset((('185.0.0', 360000), ('187.0.0', 360000)))
# Older saves (11.0-11.2: 164/181, 11.3: 183) that the Windows 11.5 engine was
# observed to render headlessly with full frame counts; Windows profile only.
WINDOWS_LEGACY_SCHEMAS = frozenset((('164.0.0', 360000), ('181.0.0', 360000), ('183.0.0', 360000)))
WINDOWS_115 = WINDOWS_PROFILE_PREFIX + '11.5.0.14471'


def validate_timeline_schema(timeline, runtime_profile=None):
    import os
    schema = (timeline.get('new_version'), timeline.get('version'))
    legacy = schema in WINDOWS_LEGACY_SCHEMAS
    if type(schema[1]) is not int or not (schema in TIMELINE_SCHEMAS or (legacy and os.name == 'nt')):
        raise ValueError('Unexpected native timeline version')
    if runtime_profile is not None:
        known = {PROFILE_PREFIX + version for version in PROFILES}
        known.add(WINDOWS_115)
        is_115 = runtime_profile in {PROFILE_PREFIX + '11.5.0', WINDOWS_115}
        if (runtime_profile not in known or (schema[0] == '187.0.0' and not is_115)
                or (legacy and runtime_profile != WINDOWS_115)):
            raise ValueError('Native timeline schema is incompatible with this runtime profile')
    return schema


def saved_schema_upgrade(expected, actual, runtime_profile):
    """Recognize only the exact observed UI upgrade; never normalize other fields."""
    before = validate_timeline_schema(expected, runtime_profile)
    after = validate_timeline_schema(actual, runtime_profile)
    if before == after:
        return None
    if (runtime_profile == PROFILE_PREFIX + '11.5.0' and before == ('185.0.0', 360000)
            and after == ('187.0.0', 360000)
            and actual.get('last_modified_platform', {}).get('app_version') == '11.5.0'):
        return {'timeline_id': actual['id'], 'before': before[0], 'after': after[0]}
    # Windows 11.5 upgrades any reviewed older schema to 187 when it saves.
    if (runtime_profile == WINDOWS_115 and before[0] in {'164.0.0', '181.0.0', '183.0.0', '185.0.0'}
            and after == ('187.0.0', 360000)
            and actual.get('last_modified_platform', {}).get('app_version') == '11.5.0'):
        return {'timeline_id': actual['id'], 'before': before[0], 'after': after[0]}
    raise ValueError('Unreviewed native schema migration')


def validate_identity(info, fingerprint):
    version = info.get('CFBundleShortVersionString')
    if (version not in PROFILES or info.get('CFBundleVersion') != version
            or info.get('CFBundleIdentifier') != 'com.lemon.lvpro'):
        raise ValueError('Unsupported Jianying version/build/identity; stop native writes')
    if fingerprint != PROFILES[version]:
        raise ValueError('Editor library differs from its exact headless runtime profile; version='
                         + version + '; actual=' + str(fingerprint) + '; expected=' + PROFILES[version]
                         + '. Collect a redacted report with python3 tools/runtime_report.py. '
                         'This build needs review; do not replace the expected hash.')
    return version


def validate_export_profiles(build_profile, runtime_profile):
    if build_profile not in EXPORT_PROFILES or runtime_profile not in EXPORT_PROFILES:
        raise ValueError('No reviewed native export ABI for this build/runtime profile')
    if build_profile != runtime_profile:
        raise ValueError('Build runtime differs from the installed editor; rebuild or edit a copy on the current runtime')


def validate_resource_profile(runtime_profile, capture_profile):
    if capture_profile != RESOURCE_CAPTURE_PROFILE or runtime_profile not in EXPORT_PROFILES:
        raise ValueError('Native resources need a reviewed runtime profile/capture pairing')
