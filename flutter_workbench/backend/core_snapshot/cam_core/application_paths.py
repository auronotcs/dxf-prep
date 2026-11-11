"""Immutable install/user path ownership; imports never create or migrate files."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Mapping

from .recovery_store import recovery_store_directory


@dataclass(frozen=True)
class ApplicationPaths:
    install_root: Path
    user_root: Path
    cache_root: Path
    legacy_root: Path | None = None

    @property
    def settings(self) -> Path:
        return self.user_root / 'cam_workbench_settings.json'

    @property
    def recovery(self) -> Path:
        return self.user_root / 'cam_workbench_recovery.camjob.json'

    def asset(self, *parts: str) -> Path:
        destination=self.install_root.joinpath(*parts).resolve()
        if not destination.is_relative_to(self.install_root):
            raise ValueError('Application assets must stay within the installation.')
        return destination


def application_paths(source_file: Path, *, install_root=None, user_root=None,
                      cache_root=None, legacy_root=None, environ: Mapping[str,str] | None=None,
                      frozen: bool | None=None, executable=None, bundle_root=None) -> ApplicationPaths:
    env=os.environ if environ is None else environ
    frozen=bool(getattr(sys,'frozen',False)) if frozen is None else frozen
    default_install=Path(bundle_root or getattr(sys,'_MEIPASS',Path(source_file).resolve().parent))
    install=Path(install_root or env.get('CAM_WORKBENCH_INSTALL_DIR') or default_install).resolve()
    overridden=user_root is not None or bool(env.get('CAM_WORKBENCH_DATA_DIR'))
    local=Path(env.get('LOCALAPPDATA') or (Path.home()/'AppData'/'Local'))
    user=Path(user_root or env.get('CAM_WORKBENCH_DATA_DIR') or local/'CAM Workbench').resolve()
    cache=Path(cache_root or env.get('CAM_WORKBENCH_CACHE_DIR') or user/'cache').resolve()
    explicit_legacy=legacy_root or env.get('CAM_WORKBENCH_LEGACY_DIR')
    # A test/staged user override is isolated by default; never pull real data
    # into it unless a specific legacy source is explicitly supplied.
    legacy=(Path(explicit_legacy).resolve() if explicit_legacy else None if overridden else
            Path(executable or sys.executable).resolve().parent if frozen else install)
    if user==install or cache==install or user.is_relative_to(install) or cache.is_relative_to(install):
        raise ValueError('Writable user/cache folders must be outside the immutable installation.')
    return ApplicationPaths(install,user,cache,legacy)


def _publish_missing(destination: Path, content: bytes, *, source_times: tuple[int,int] | None = None) -> bool:
    """Atomic no-clobber publication on the destination filesystem."""
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with NamedTemporaryFile(dir=destination.parent,prefix='.migration-',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if source_times is not None:
            os.utime(temporary,ns=source_times)
        try:
            os.link(temporary,destination)
        except FileExistsError:
            return False
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def migrate_legacy_user_files(paths: ApplicationPaths) -> tuple[str,...]:
    """One completed import, byte-preserving and never overwriting/deleting sources.

    Startup must finish this before consuming recovery. On an interrupted or
    concurrent migration, fail closed rather than risk importing consumed files.
    """
    legacy=paths.legacy_root
    if legacy is None or legacy==paths.user_root:
        return ()
    marker=paths.user_root/'legacy-migration-v1.json'
    if marker.exists():
        return ()
    settings=legacy/paths.settings.name
    recovery=legacy/paths.recovery.name
    sources=[settings,settings.with_name(settings.name+'.bak'),recovery]
    store=recovery_store_directory(recovery)
    if store.is_dir():
        sources.extend(sorted(p for p in store.iterdir() if p.name.casefold().endswith('.camjob.json')))
    sources=[source for source in sources if source.is_file()]
    if not sources:
        # Launching an asset-only checkpoint must not suppress a later import
        # from the actual legacy installation containing the user's files.
        return ()
    paths.user_root.mkdir(parents=True,exist_ok=True)
    lock=paths.user_root/'legacy-migration-v1.lock'
    try:
        descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError as exc:
        raise OSError(f'Legacy migration is running or was interrupted. Review {lock} before restarting; originals are untouched.') from exc
    os.close(descriptor)
    try:
        if marker.exists():
            return ()
        records=[]
        user_settings_exist=paths.settings.exists() or paths.settings.with_name(paths.settings.name+'.bak').exists()
        for source in sources:
            if not source.is_file():
                continue
            # Refuse links/junctions escaping the explicit legacy location.
            if not source.resolve().is_relative_to(legacy.resolve()):
                raise OSError(f'Legacy file resolves outside the selected migration source: {source}')
            destination=paths.user_root/source.relative_to(legacy)
            # Settings and backup are one family: don't pair a user's primary
            # with an unrelated legacy backup just because that slot is empty.
            preserve_settings=user_settings_exist and source in {settings,settings.with_name(settings.name+'.bak')}
            stat=source.stat()
            copied=False if preserve_settings or destination.exists() else _publish_missing(
                destination,source.read_bytes(),source_times=(stat.st_atime_ns,stat.st_mtime_ns))
            records.append({'source':str(source),'destination':str(destination),'copied':copied})
        _publish_missing(marker,(json.dumps({'schema':1,'files':records},indent=2)+'\n').encode())
        return tuple(row['destination'] for row in records if row['copied'])
    finally:
        lock.unlink(missing_ok=True)
