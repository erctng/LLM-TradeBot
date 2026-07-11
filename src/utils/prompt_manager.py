import os
import json
import logging
import shutil
from typing import Dict, Optional, Callable
from datetime import datetime

log = logging.getLogger(__name__)

class PromptManager:
    """
    Manager for dynamic agent prompts.
    Handles loading, saving, and versioning of system prompts for LLM agents.
    """
    
    # Directory to store active dynamic prompts
    PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "dynamic_prompts")
    # Directory to store prompt history for rollbacks
    HISTORY_DIR = os.path.join(PROMPTS_DIR, "history")
    
    _defaults: Dict[str, Callable[[], str]] = {}

    @classmethod
    def _ensure_dirs(cls):
        """Ensure directories exist"""
        os.makedirs(cls.PROMPTS_DIR, exist_ok=True)
        os.makedirs(cls.HISTORY_DIR, exist_ok=True)

    @classmethod
    def register_default(cls, agent_name: str, default_func: Callable[[], str]):
        """
        Register a default prompt fallback for an agent.
        """
        cls._defaults[agent_name] = default_func

    @classmethod
    def get_prompt(cls, agent_name: str) -> str:
        """
        Get the current active prompt for the agent.
        Falls back to default if no dynamic prompt is set.
        """
        cls._ensure_dirs()
        prompt_path = os.path.join(cls.PROMPTS_DIR, f"{agent_name}_prompt.txt")
        
        if os.path.exists(prompt_path):
            try:
                with open(prompt_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception as e:
                log.error(f"Failed to read dynamic prompt for {agent_name}: {e}")
                
        # Fallback to default
        if agent_name in cls._defaults:
            return cls._defaults[agent_name]()
            
        log.warning(f"No prompt found for {agent_name} and no default registered.")
        return ""

    @classmethod
    def update_prompt(cls, agent_name: str, new_prompt: str, reason: str = "Meta-Agent Optimization") -> bool:
        """
        Update an agent's prompt, backing up the old one for potential rollback.
        """
        cls._ensure_dirs()
        prompt_path = os.path.join(cls.PROMPTS_DIR, f"{agent_name}_prompt.txt")
        
        # Backup current if exists
        if os.path.exists(prompt_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(cls.HISTORY_DIR, f"{agent_name}_prompt_{timestamp}.txt")
            
            meta_path = os.path.join(cls.HISTORY_DIR, f"{agent_name}_meta_{timestamp}.json")
            try:
                shutil.copy2(prompt_path, backup_path)
                with open(meta_path, 'w', encoding='utf-8') as f:
                    json.dump({"replaced_at": timestamp, "reason": reason}, f)
            except Exception as e:
                log.error(f"Failed to backup prompt for {agent_name}: {e}")
                return False
                
        # Write new prompt
        try:
            with open(prompt_path, 'w', encoding='utf-8') as f:
                f.write(new_prompt.strip())
            log.info(f"✨ Successfully updated prompt for {agent_name} (Reason: {reason})")
            return True
        except Exception as e:
            log.error(f"Failed to write new prompt for {agent_name}: {e}")
            return False

    @classmethod
    def rollback_prompt(cls, agent_name: str) -> bool:
        """
        Rollback an agent's prompt to its previous version.
        """
        cls._ensure_dirs()
        prompt_path = os.path.join(cls.PROMPTS_DIR, f"{agent_name}_prompt.txt")
        
        # Find latest backup
        backups = [f for f in os.listdir(cls.HISTORY_DIR) if f.startswith(f"{agent_name}_prompt_")]
        if not backups:
            log.warning(f"No backup history for {agent_name}. Deleting dynamic prompt to restore hardcoded default.")
            if os.path.exists(prompt_path):
                os.remove(prompt_path)
            return True
            
        backups.sort(reverse=True)
        latest_backup = backups[0]
        backup_path = os.path.join(cls.HISTORY_DIR, latest_backup)
        
        try:
            shutil.copy2(backup_path, prompt_path)
            log.info(f"⏪ Successfully rolled back prompt for {agent_name} from {latest_backup}")
            return True
        except Exception as e:
            log.error(f"Failed to rollback prompt for {agent_name}: {e}")
            return False

    @classmethod
    def get_all_active_prompts(cls) -> Dict[str, str]:
        """
        Returns a dict of all currently active prompts.
        """
        result = {}
        for agent_name in cls._defaults.keys():
            result[agent_name] = cls.get_prompt(agent_name)
        return result
