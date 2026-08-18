"""
Garde-fous d'exécution en continu.

Trois défauts observés sur un conteneur en fonctionnement : le bot ne démarrait
jamais faute de dashboard, il reconstruisait tous ses agents une fois par
seconde, et rien n'empêchait deux instances d'écrire le même fichier de trades.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


# --------------------------------------------------------------------------
# Reconstruction des agents à chaque tour
# --------------------------------------------------------------------------

def test_enabled_agent_map_is_stable_under_renormalisation():
    """La condition de reconfiguration doit se stabiliser.

    La boucle comparait `global_state.agent_settings['agents']` (brut, tous les
    agents avec true/false) à `_last_agent_config` (normalisé, agents activés
    seulement). Les deux formes ne coïncident jamais, donc la condition était
    toujours vraie et `agent_provider.reload()` s'exécutait à chaque tour —
    une fois par seconde, sans qu'aucun cycle de trading ne démarre.
    """
    from src.agents.agent_config import AgentConfig

    raw = {'trend_agent_llm': True, 'sentiment_agent': False, 'predict_agent': True}

    first = AgentConfig.from_dict({'agents': raw}).get_enabled_agents()
    second = AgentConfig.from_dict({'agents': first}).get_enabled_agents()

    assert first == second, (
        "normaliser deux fois doit converger, sinon la garde de reconfiguration "
        "se redéclenche indéfiniment"
    )


def test_raw_and_normalised_forms_actually_differ():
    """Documente pourquoi la comparaison directe était fautive."""
    from src.agents.agent_config import AgentConfig

    raw = {'trend_agent_llm': True, 'sentiment_agent': False}
    normalised = AgentConfig.from_dict({'agents': raw}).get_enabled_agents()

    assert raw != normalised


def test_loop_normalises_before_comparing():
    """Garde de couplage sur le correctif lui-même."""
    source = open(os.path.join(REPO_ROOT, 'src/trading/multi_agent_trading_bot.py')).read()
    marker = "runtime_agents = runtime_settings.get('agents', {})"
    assert marker in source

    after = source[source.index(marker):source.index(marker) + 1200]
    assert 'get_enabled_agents()' in after, (
        "la configuration runtime doit être normalisée avant comparaison"
    )
    assert 'runtime_agents != self._last_agent_config' not in after, (
        "comparaison brute/normalisée réintroduite : la boucle repartira à 1 Hz"
    )


# --------------------------------------------------------------------------
# Démarrage sans dashboard
# --------------------------------------------------------------------------

def test_autostart_flag_exists():
    """`--headless --mode continuous` sans autostart ne peut jamais trader."""
    result = subprocess.run(
        [sys.executable, 'main.py', '--help'],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert '--autostart' in result.stdout
    assert '--allow-concurrent' in result.stdout


def test_headless_without_autostart_is_warned_about():
    source = open(os.path.join(REPO_ROOT, 'main.py')).read()
    assert 'AUTOSTART' in source
    assert 'no dashboard' in source.lower() or 'press Start' in source


# --------------------------------------------------------------------------
# Verrou d'instance unique
# --------------------------------------------------------------------------

@pytest.fixture
def lock_path(tmp_path):
    return str(tmp_path / 'bot.lock')


def test_lock_is_acquired_when_free(lock_path):
    from main import acquire_instance_lock

    handle = acquire_instance_lock(lock_path)
    assert handle
    handle.close()


def test_second_instance_is_refused(lock_path):
    """Le cas réel : conteneur Docker et processus hôte sur le même volume.

    `flock` porte sur l'inode et traverse donc le bind mount, là où une
    comparaison de PID échouerait — les espaces de noms sont distincts.
    """
    from main import acquire_instance_lock

    first = acquire_instance_lock(lock_path)
    assert first

    probe = subprocess.run(
        [sys.executable, '-c',
         'import sys; sys.path.insert(0, %r)\n'
         'from main import acquire_instance_lock\n'
         'print("BLOCKED" if acquire_instance_lock(%r) is None else "PASSED")'
         % (REPO_ROOT, lock_path)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    assert 'BLOCKED' in probe.stdout, probe.stdout + probe.stderr[-400:]

    first.close()


def test_lock_is_released_on_close(lock_path):
    from main import acquire_instance_lock

    first = acquire_instance_lock(lock_path)
    assert first
    first.close()

    second = acquire_instance_lock(lock_path)
    assert second, "le verrou doit être libéré à la fermeture du descripteur"
    second.close()
