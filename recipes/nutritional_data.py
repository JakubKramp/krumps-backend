from typing import Dict

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from recipes.constants import NUTRITIONAL_VALUES_MAPPING
from recipes.models import Ingredient
from recipes.schemas import NutritionalValues


class NutritionalAPIClient:
    apikey = settings.NUTRITION_APIKEY
    base_url = settings.NUTRITION_API_URL


    async def construct_url(self, ingredient_name:str, endpoint: str = 'search?') -> str:
        return ''.join([self.base_url, endpoint, f'query={ingredient_name}', f'&api_key={self.apikey}'])

    async def search_for_ingredients(self, ingredient_name: str) -> Dict[str, str]:
        url = await self.construct_url(ingredient_name)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                response_data = await response.json()
                return response_data

    @staticmethod
    async def process_value(nutrient: dict) -> int:
        if nutrient['unitName'] == 'G':
            return nutrient['value']
        elif nutrient['unitName'] == 'MG':
            return nutrient['value']/1000
        return 0

    async def extract_nutritional_values(self, ingredient_name: str) -> NutritionalValues:
        data = await self.search_for_ingredients(ingredient_name)
        nutri_data = {
            NUTRITIONAL_VALUES_MAPPING[nutrient['nutrientName']]: await self.process_value(nutrient)
            for nutrient in data['foods'][0]['foodNutrients'] if nutrient['nutrientName'] in NUTRITIONAL_VALUES_MAPPING.keys()# type: ignore
        }
        return NutritionalValues(**nutri_data)


    async def fill_nutritional_values(self, ingredient: Ingredient, session: AsyncSession) -> Ingredient:
        nutri_data = await self.extract_nutritional_values(ingredient.name)
        for field, value in nutri_data.model_dump().items():
            setattr(ingredient, field, value)

        session.add(ingredient)
        await session.commit()
        await session.refresh(ingredient)

        return ingredient
