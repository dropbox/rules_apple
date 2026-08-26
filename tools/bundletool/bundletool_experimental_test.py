#!/usr/bin/python3
# Copyright 2026 The Bazel Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for the experimental tree-artifact bundler."""

import os
import shutil
import stat
import tempfile
import unittest
import zipfile

from tools.bundletool import bundletool_experimental


class BundlerExperimentalTest(unittest.TestCase):

  def setUp(self):
    super().setUp()
    self._scratch_dir = tempfile.mkdtemp("bundlerExperimentalScratch")

  def tearDown(self):
    super().tearDown()
    shutil.rmtree(self._scratch_dir)

  def _run_bundler(self, control):
    output = os.path.join(self._scratch_dir, "output")
    control["output"] = output
    bundletool_experimental.Bundler(control).run()
    return output

  def _scratch_file(self, path, content=""):
    full_path = os.path.join(self._scratch_dir, path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as fp:
      fp.write(content)
    return full_path

  def _scratch_symlink(self, path, target):
    full_path = os.path.join(self._scratch_dir, path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    os.symlink(target, full_path)
    return full_path

  def _scratch_zip(self, name):
    path = os.path.join(self._scratch_dir, name)
    with zipfile.ZipFile(path, "w") as zf:
      self._add_zip_file(
          zf, "Foo.framework/Versions/A/Foo", "framework-binary")
      self._add_zip_file(
          zf,
          "Foo.framework/Versions/A/Resources/Info.plist",
          "plist-content",
      )
      self._add_zip_symlink(
          zf, "Foo.framework/Versions/Current", "A")
      self._add_zip_symlink(
          zf, "Foo.framework/Foo", "Versions/Current/Foo")
      self._add_zip_symlink(
          zf, "Foo.framework/Resources", "Versions/Current/Resources")
    return path

  def _add_zip_file(self, zf, path, content):
    zipinfo = zipfile.ZipInfo(path)
    zipinfo.external_attr = (stat.S_IFREG | 0o644) << 16
    zipinfo.compress_type = zipfile.ZIP_STORED
    zf.writestr(zipinfo, content)

  def _add_zip_symlink(self, zf, path, target):
    zipinfo = zipfile.ZipInfo(path)
    zipinfo.external_attr = (stat.S_IFLNK | 0o777) << 16
    zipinfo.compress_type = zipfile.ZIP_STORED
    zf.writestr(zipinfo, target)

  def _assert_symlink(self, path, target):
    self.assertTrue(os.path.islink(path), msg=f"{path} should be a symlink")
    self.assertEqual(target, os.readlink(path))

  def test_bundle_merge_files_preserves_symlinked_files_and_directories(self):
    framework_root = os.path.join(self._scratch_dir, "Foo.framework")
    self._scratch_file(
        "Foo.framework/Versions/A/Foo", "framework-binary")
    self._scratch_file(
        "Foo.framework/Versions/A/Resources/Info.plist", "plist-content")
    self._scratch_symlink("Foo.framework/Versions/Current", "A")
    self._scratch_symlink("Foo.framework/Foo", "Versions/Current/Foo")
    self._scratch_symlink(
        "Foo.framework/Resources", "Versions/Current/Resources")

    output = self._run_bundler({
        "bundle_merge_files": [{
            "src": framework_root,
            "dest": "Contents/Frameworks/Foo.framework",
        }],
    })

    bundled_framework = os.path.join(output, "Contents/Frameworks/Foo.framework")
    self.assertTrue(os.path.isfile(os.path.join(
        bundled_framework, "Versions/A/Foo")))
    self.assertTrue(os.path.isfile(os.path.join(
        bundled_framework, "Versions/A/Resources/Info.plist")))
    self._assert_symlink(os.path.join(
        bundled_framework, "Versions/Current"), "A")
    self._assert_symlink(os.path.join(
        bundled_framework, "Foo"), "Versions/Current/Foo")
    self._assert_symlink(os.path.join(
        bundled_framework, "Resources"), "Versions/Current/Resources")

  def test_bundle_symlinks(self):
    output = self._run_bundler({
        "bundle_symlinks": [
            {"dest": "Foo.framework/Versions/Current", "target": "A"},
            {
                "dest": "Foo.framework/Foo",
                "target": "Versions/Current/Foo",
            },
            {
                "dest": "Foo.framework/Resources",
                "target": "Versions/Current/Resources",
            },
        ],
    })

    bundled_framework = os.path.join(output, "Foo.framework")
    self._assert_symlink(os.path.join(
        bundled_framework, "Versions/Current"), "A")
    self._assert_symlink(os.path.join(
        bundled_framework, "Foo"), "Versions/Current/Foo")
    self._assert_symlink(os.path.join(
        bundled_framework, "Resources"), "Versions/Current/Resources")

  def test_bundle_symlinks_reject_invalid_paths_and_targets(self):
    for dest in ["../outside", "Versions/../Current"]:
      with self.subTest(dest=dest):
        with self.assertRaises(bundletool_experimental.BundlePathError):
          self._run_bundler({
              "bundle_symlinks": [
                  {"dest": dest, "target": "target"},
              ],
          })

    for target in ["/tmp/outside", "../../outside", "Versions/../A"]:
      with self.subTest(target=target):
        with self.assertRaises(bundletool_experimental.BundleSymlinkError):
          self._run_bundler({
              "bundle_symlinks": [
                  {"dest": "Versions/Current", "target": target},
              ],
          })

  def test_bundle_symlink_conflicts_with_existing_regular_file(self):
    regular_file = self._scratch_file("regular", "content")
    with self.assertRaises(bundletool_experimental.BundleConflictError):
      self._run_bundler({
          "bundle_merge_files": [{
              "src": regular_file,
              "dest": "Alias",
          }],
          "bundle_symlinks": [{
              "dest": "Alias",
              "target": "Target",
          }],
      })

  def test_bundle_regular_file_conflicts_with_existing_symlink(self):
    symlink_zip = os.path.join(self._scratch_dir, "symlink.zip")
    with zipfile.ZipFile(symlink_zip, "w") as zf:
      self._add_zip_symlink(zf, "Alias", "Target")

    regular_file = self._scratch_file("regular", "content")
    with self.assertRaises(bundletool_experimental.BundleConflictError):
      self._run_bundler({
          "bundle_merge_zips": [{
              "src": symlink_zip,
              "dest": "",
          }],
          "bundle_merge_files": [{
              "src": regular_file,
              "dest": "Alias",
          }],
      })

  def test_bundle_zip_file_conflicts_with_existing_symlink(self):
    symlink_zip = os.path.join(self._scratch_dir, "symlink.zip")
    with zipfile.ZipFile(symlink_zip, "w") as zf:
      self._add_zip_symlink(zf, "Alias", "Target")

    regular_zip = os.path.join(self._scratch_dir, "regular.zip")
    with zipfile.ZipFile(regular_zip, "w") as zf:
      self._add_zip_file(zf, "Alias", "content")

    with self.assertRaises(bundletool_experimental.BundleConflictError):
      self._run_bundler({
          "bundle_merge_zips": [
              {"src": symlink_zip, "dest": ""},
              {"src": regular_zip, "dest": ""},
          ],
      })

  def test_bundle_merge_zips_preserves_symlink_entries(self):
    framework_zip = self._scratch_zip("Foo.zip")

    output = self._run_bundler({
        "bundle_merge_zips": [{
            "src": framework_zip,
            "dest": "Contents/Frameworks",
        }],
    })

    bundled_framework = os.path.join(output, "Contents/Frameworks/Foo.framework")
    self.assertTrue(os.path.isfile(os.path.join(
        bundled_framework, "Versions/A/Foo")))
    self.assertTrue(os.path.isfile(os.path.join(
        bundled_framework, "Versions/A/Resources/Info.plist")))
    self._assert_symlink(os.path.join(
        bundled_framework, "Versions/Current"), "A")
    self._assert_symlink(os.path.join(
        bundled_framework, "Foo"), "Versions/Current/Foo")
    self._assert_symlink(os.path.join(
        bundled_framework, "Resources"), "Versions/Current/Resources")

  def test_bundle_merge_zips_rejects_absolute_symlink_targets(self):
    framework_zip = os.path.join(self._scratch_dir, "Foo.zip")
    with zipfile.ZipFile(framework_zip, "w") as zf:
      self._add_zip_symlink(zf, "Foo.framework/Foo", "/tmp/outside")

    with self.assertRaisesRegex(
        bundletool_experimental.BundleSymlinkError,
        r"Cannot create bundle symlink .* escapes the bundle root",
    ):
      self._run_bundler({
          "bundle_merge_zips": [{
              "src": framework_zip,
              "dest": "Contents/Frameworks",
          }],
      })

  def test_bundle_merge_zips_rejects_relative_symlink_targets_that_escape(self):
    framework_zip = os.path.join(self._scratch_dir, "Foo.zip")
    with zipfile.ZipFile(framework_zip, "w") as zf:
      self._add_zip_symlink(zf, "Foo.framework/Foo", "../../../../outside")

    with self.assertRaisesRegex(
        bundletool_experimental.BundleSymlinkError,
        r"Cannot create bundle symlink .* escapes the bundle root",
    ):
      self._run_bundler({
          "bundle_merge_zips": [{
              "src": framework_zip,
              "dest": "Contents/Frameworks",
          }],
      })

  def test_bundle_merge_zips_rejects_writes_through_escaping_symlink_ancestors(self):
    framework_zip = os.path.join(self._scratch_dir, "Foo.zip")
    with zipfile.ZipFile(framework_zip, "w") as zf:
      self._add_zip_symlink(zf, "Foo", "bar")
      self._add_zip_file(zf, "bar/.keep", "")
      self._add_zip_file(zf, "Foo/Contents/file.txt", "payload")

    output = self._run_bundler({
        "bundle_merge_zips": [{
            "src": framework_zip,
            "dest": "",
        }],
    })

    self._assert_symlink(os.path.join(output, "Foo"), "bar")
    self.assertTrue(os.path.isfile(os.path.join(output, "bar/Contents/file.txt")))

    escaping_zip = os.path.join(self._scratch_dir, "Escaping.zip")
    with zipfile.ZipFile(escaping_zip, "w") as zf:
      self._add_zip_symlink(zf, "Foo", "../outside")
      self._add_zip_file(zf, "Foo/Contents/file.txt", "payload")

    with self.assertRaisesRegex(
        bundletool_experimental.BundleSymlinkError,
        r"Cannot create bundle symlink .* escapes the bundle root",
    ):
      self._run_bundler({
          "bundle_merge_zips": [{
              "src": escaping_zip,
              "dest": "",
          }],
      })

if __name__ == "__main__":
  unittest.main()
