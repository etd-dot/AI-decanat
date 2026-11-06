#include "DecanatMetaHumanTools.h"
#include "Engine/SkeletalMesh.h"
#include "Materials/MaterialInterface.h"
#include "MeshDescription.h"
#include "SkeletalMeshAttributes.h"
#include "StaticToSkeletalMeshConverter.h"
#include "BoneWeights.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"

UObject* UDecanatMetaHumanTools::CreateOutfit(UObject* Asset, const FString& JsonFile)
{
    auto* Body = Cast<USkeletalMesh>(Asset);
    FString Text;
    TSharedPtr<FJsonObject> Data;
    if (!Body || !FFileHelper::LoadFileToString(Text, *JsonFile) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Data) || !Data) return nullptr;
    const FString Path = Data->GetStringField(TEXT("asset"));
    if (!Path.StartsWith(TEXT("/Game/Decanat/Wardrobe/")) || FPackageName::DoesPackageExist(Path) || FindObject<UPackage>(nullptr, *Path)) return nullptr;
    TArray<FSkeletalMaterial> Materials;
    for (const auto& Value : Data->GetArrayField(TEXT("materials"))) {
        auto Item = Value->AsObject();
        auto* Interface = LoadObject<UMaterialInterface>(nullptr, *Item->GetStringField(TEXT("asset")));
        if (!Interface) return nullptr;
        FSkeletalMaterial Material;
        Material.MaterialInterface = Interface;
        Material.MaterialSlotName = FName(*Item->GetStringField(TEXT("name")));
        Material.ImportedMaterialSlotName = Material.MaterialSlotName;
        Materials.Add(Material);
    }
    FMeshDescription Description;
    FSkeletalMeshAttributes Attributes(Description);
    Attributes.Register();
    const FReferenceSkeleton& Skeleton = Body->GetRefSkeleton();
    for (int32 Index = 0; Index < Skeleton.GetRawBoneNum(); ++Index) {
        const FBoneID Bone = Attributes.CreateBone();
        Attributes.GetBoneNames().Set(Bone, Skeleton.GetBoneName(Index));
        Attributes.GetBoneParentIndices().Set(Bone, Skeleton.GetParentIndex(Index));
        Attributes.GetBonePoses().Set(Bone, Skeleton.GetRefBonePose()[Index]);
    }
    auto Positions = Attributes.GetVertexPositions();
    auto SkinWeights = Attributes.GetVertexSkinWeights();
    auto UVs = Attributes.GetVertexInstanceUVs();
    UVs.SetNumChannels(1);
    TArray<FPolygonGroupID> Groups;
    for (const auto& Material : Materials) {
        auto Group = Description.CreatePolygonGroup();
        Attributes.GetPolygonGroupMaterialSlotNames().Set(Group, Material.MaterialSlotName);
        Groups.Add(Group);
    }
    const auto& Vertices = Data->GetArrayField(TEXT("vertices_cm"));
    const auto& Weights = Data->GetArrayField(TEXT("weights"));
    if (Vertices.Num() != Weights.Num() || Vertices.Num() > 200000) return nullptr;
    TArray<FVertexID> IDs;
    for (int32 Index = 0; Index < Vertices.Num(); ++Index) {
        const auto& P = Vertices[Index]->AsArray();
        if (P.Num() != 3) return nullptr;
        const auto ID = Description.CreateVertex();
        Positions.Set(ID, FVector3f(P[0]->AsNumber(), P[1]->AsNumber(), P[2]->AsNumber()));
        TArray<UE::AnimationCore::FBoneWeight> Influences;
        for (const auto& Value : Weights[Index]->AsArray()) {
            const auto& W = Value->AsArray();
            const int32 Bone = static_cast<int32>(W[0]->AsNumber());
            if (Bone < 0 || Bone >= Skeleton.GetNum()) return nullptr;
            Influences.Emplace(Bone, static_cast<float>(W[1]->AsNumber()));
        }
        if (Influences.IsEmpty()) return nullptr;
        SkinWeights.Set(ID, UE::AnimationCore::FBoneWeights::Create(Influences));
        IDs.Add(ID);
    }
    for (const auto& Value : Data->GetArrayField(TEXT("triangles"))) {
        const auto& T = Value->AsArray();
        if (T.Num() != 4) return nullptr;
        const int32 Group = static_cast<int32>(T[3]->AsNumber());
        if (!Groups.IsValidIndex(Group)) return nullptr;
        TArray<FVertexInstanceID> Instances;
        for (int32 Corner = 0; Corner < 3; ++Corner) {
            const int32 Vertex = static_cast<int32>(T[Corner]->AsNumber());
            if (!IDs.IsValidIndex(Vertex)) return nullptr;
            auto Instance = Description.CreateVertexInstance(IDs[Vertex]);
            const FVector3f P = Positions.Get(IDs[Vertex]);
            UVs.Set(Instance, 0, FVector2f(P.X * 0.025f, P.Z * 0.025f));
            Attributes.GetVertexInstanceColors().Set(Instance, FVector4f(1, 1, 1, 1));
            Instances.Add(Instance);
        }
        Description.CreateTriangle(Groups[Group], Instances);
    }
    auto* Package = CreatePackage(*Path);
    auto* Mesh = NewObject<USkeletalMesh>(Package, *FPackageName::GetLongPackageAssetName(Path), RF_Public | RF_Standalone);
    TArray<const FMeshDescription*> Descriptions{&Description};
    // Copy the exact body bind skeleton; never merge or edit the shared skeleton.
    if (!FStaticToSkeletalMeshConverter::InitializeSkeletalMeshFromMeshDescriptions(Mesh, Descriptions, Materials, Skeleton, true, true)) return nullptr;
    Mesh->SetSkeleton(Body->GetSkeleton());
    Mesh->MarkPackageDirty();
    FAssetRegistryModule::AssetCreated(Mesh);
    return Mesh;
}
