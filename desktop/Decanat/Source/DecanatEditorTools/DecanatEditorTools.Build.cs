using UnrealBuildTool;

public class DecanatEditorTools : ModuleRules
{
    public DecanatEditorTools(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new string[] {
            "UnrealEd", "Json", "MetaHumanCharacter", "MetaHumanCharacterEditor", "MetaHumanSDKEditor",
            "MetaHumanCharacterPalette", "AssetRegistry", "MeshDescription", "StaticMeshDescription",
            "SkeletalMeshDescription", "SkeletalMeshUtilitiesCommon", "AnimationCore", "Decanat",
            "BlueprintGraph", "AnimGraph", "AnimGraphRuntime"
        });
        if (System.Environment.GetEnvironmentVariable("DECANAT_WITH_ACE") == "1")
            PrivateDependencyModuleNames.Add("ACERuntime");
    }
}
