"""Version selection and fail-closed guards, without changing the installed app."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'engine'))
import runtime_profiles as profiles


class RuntimeProfiles(unittest.TestCase):
    def info(self, version):
        return {'CFBundleShortVersionString': version, 'CFBundleVersion': version,
                'CFBundleIdentifier': 'com.lemon.lvpro'}

    def test_primary_and_legacy_identities(self):
        self.assertEqual(profiles.PRIMARY_VERSION, '11.5.0')
        for v,h in profiles.PROFILES.items():
            self.assertEqual(profiles.validate_identity(self.info(v),h),v)

    def test_exact_1142_fingerprint_is_preserved(self):
        self.assertEqual(profiles.PROFILES['11.4.2'],
                         '632c8ddd09ff4a54f876cd8142eb505055ee26d944199506b230949b7e106bd1')

    def test_mismatched_hash_build_bundle_and_unknown_version_rejected(self):
        for version in ('11.5.0','11.4.2'):
            h=profiles.PROFILES[version]
            bad=[(self.info(version),'0'*64)]
            for key,val in [('CFBundleVersion','unexpected'),('CFBundleIdentifier','com.other')]:
                info=self.info(version);info[key]=val;bad.append((info,h))
            for info,sha in bad:
                with self.subTest(version=version,info=info),self.assertRaises(ValueError):
                    profiles.validate_identity(info,sha)
        with self.assertRaises(ValueError):
            profiles.validate_identity(self.info('11.5.1'),profiles.PROFILES['11.5.0'])

    def test_export_only_allows_same_reviewed_runtime(self):
        for v in ('11.5.0','11.4.2'):
            p=profiles.PROFILE_PREFIX+v
            profiles.validate_export_profiles(p,p)
        for a,b in [('11.4.2','11.5.0'),('11.5.0','11.4.2'),('11.4.0','11.4.0'),('11.5.1','11.5.1')]:
            with self.subTest(a=a,b=b),self.assertRaises(ValueError):
                profiles.validate_export_profiles(profiles.PROFILE_PREFIX+a,profiles.PROFILE_PREFIX+b)

    def test_resource_pairing_keeps_capture_provenance(self):
        for p in profiles.EXPORT_PROFILES:
            profiles.validate_resource_profile(p,profiles.RESOURCE_CAPTURE_PROFILE)
        with self.assertRaises(ValueError):
            profiles.validate_resource_profile(profiles.PROFILE_PREFIX+'11.4.0',profiles.RESOURCE_CAPTURE_PROFILE)
        with self.assertRaises(ValueError):
            profiles.validate_resource_profile(profiles.PROFILE_PREFIX+'11.5.0',profiles.PROFILE_PREFIX+'11.5.0')

    def test_timeline_schema_is_version_scoped(self):
        old = {'new_version':'185.0.0','version':360000}
        new = {'new_version':'187.0.0','version':360000}
        for version in ('11.4.0','11.4.2','11.5.0'):
            profiles.validate_timeline_schema(old,profiles.PROFILE_PREFIX+version)
        profiles.validate_timeline_schema(new,profiles.PROFILE_PREFIX+'11.5.0')
        for version in ('11.4.0','11.4.2','11.5.1'):
            with self.assertRaises(ValueError):
                profiles.validate_timeline_schema(new,profiles.PROFILE_PREFIX+version)
        for bad in [dict(new,new_version='188.0.0'),dict(new,version=360001),dict(new,version=360000.0)]:
            with self.assertRaises(ValueError): profiles.validate_timeline_schema(bad)

    def test_ui_upgrade_is_reported_without_mutating_evidence(self):
        old={'id':'sample','new_version':'185.0.0','version':360000}
        new=dict(old,new_version='187.0.0',last_modified_platform={'app_version':'11.5.0'})
        runtime=profiles.PROFILE_PREFIX+'11.5.0'
        change=profiles.saved_schema_upgrade(old,new,runtime)
        self.assertEqual(change,{'timeline_id':'sample','before':'185.0.0','after':'187.0.0'})
        self.assertEqual(new['new_version'],'187.0.0')
        self.assertIsNone(profiles.saved_schema_upgrade(new,new,runtime))
        with self.assertRaises(ValueError): profiles.saved_schema_upgrade(new,old,runtime)
        with self.assertRaises(ValueError):
            profiles.saved_schema_upgrade(old,dict(new,last_modified_platform={}),runtime)


if __name__=='__main__':
    unittest.main()
