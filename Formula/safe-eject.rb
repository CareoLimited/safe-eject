# Homebrew formula for SafeEject. This repository doubles as its tap:
#   brew tap careolimited/safe-eject https://github.com/CareoLimited/safe-eject
#   brew install safe-eject
#
# For each release: push a tag (v1.0.0), then run scripts/update-formula.sh with
# the version to fill in the url and sha256 below, and commit the result.
class SafeEject < Formula
  desc "See what keeps a macOS disk busy, close it, and eject it safely"
  homepage "https://github.com/CareoLimited/safe-eject"
  url "https://github.com/CareoLimited/safe-eject/archive/refs/tags/v1.0.0.tar.gz"
  sha256 "55da962e7eb236f4cb39a4a258d5cce3099fe28c1ae4bf4c756651e6fcbccd88"
  license "MIT"

  depends_on :macos

  def install
    libexec.install "safe_eject.py", "web"
    # Homebrew needs the Command Line Tools, so the system Python 3 is always there
    (bin/"safe-eject").write <<~SH
      #!/bin/bash
      exec /usr/bin/python3 "#{libexec}/safe_eject.py" "$@"
    SH
    chmod 0755, bin/"safe-eject"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/safe-eject --version")
  end
end
